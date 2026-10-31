"""单元接缝:知识库文档清单在真库上的语义与内存基线一致(T17)。

同一组筛选与分页参数下,PostgreSQL 实现与内存实现返回**相同的文档集合、
相同顺序、相同总数**;应用主接缝验证清单端点在 pgvector 装配下走通。
后端不可达(无 Docker)时整批跳过:test_pg_database 夹具负责。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.domain.documents import DocumentFilter, DocumentStatus
from app.integrations.llm.deterministic import DeterministicEmbedder
from app.main import create_app
from app.repositories.memory import InMemoryDocumentRepository
from app.repositories.postgres import PgDatabase, PgDocumentRepository

DOCUMENTS_URL = "/api/v1/documents"

_BASE = datetime(2026, 10, 30, 8, 0, tzinfo=UTC)


def _document(file_name: str, *, minutes_ago: int = 0, **overrides):
    """构造一份档案:``minutes_ago`` 越大则创建时间越早。"""
    from app.domain.documents import DocumentRecord

    return DocumentRecord(
        file_name=file_name,
        file_type="txt",
        size_bytes=32,
        created_at=_BASE - timedelta(minutes=minutes_ago),
        **overrides,
    )


def _seed() -> list:
    """一组覆盖各种口径的档案:版本退位、文档删除、异租户、异状态、不同创建时间。

    返回值按创建时间从早到晚,便于断言排序。

    注:``handbook.txt`` 有两条现行版本(40 分钟前与 0 分钟前)看似违反
    "同一标识只留一个现行版本"的业务不变量,但仓储契约只按 ``is_latest``
    标志过滤,不校验该不变量 —— 这里刻意保留,验证契约边界不被悄然扩大。

    退位版本(``is_latest=False`` + ``superseded``)与已删除文档(``is_latest=False``
    + ``deleted``)都靠 ``is_latest`` 退出默认清单 —— 后者与
    :meth:`app.services.lifecycle.DocumentLifecycleService.delete` 的落点一致。
    """
    return [
        _document("handbook.txt", minutes_ago=40, tenant="acme", department="人力部"),
        _document("guide.txt", minutes_ago=30, tenant="acme", doc_type="手册"),
        _document("notes.txt", minutes_ago=20, tenant="beta", status=DocumentStatus.FAILED),
        _document(
            "handbook.txt",
            minutes_ago=10,
            tenant="acme",
            is_latest=False,
            status=DocumentStatus.SUPERSEDED,
        ),
        _document(
            "obsolete.txt",
            minutes_ago=5,
            tenant="acme",
            is_latest=False,
            status=DocumentStatus.DELETED,
        ),
        _document("handbook.txt", minutes_ago=0, tenant="acme"),
    ]


def _fill(repository, records) -> None:
    for record in records:
        repository.add(record)


# ===== 两种实现同口径 =====


def test_pg_list_matches_memory_for_every_filter(test_pg_database):
    """同一组筛选与分页参数下,两种实现返回相同集合、相同顺序、相同总数。"""
    db = PgDatabase(test_pg_database.dsn)
    try:
        pg = PgDocumentRepository(db)
        memory = InMemoryDocumentRepository()
        records = _seed()
        _fill(pg, records)
        _fill(memory, records)

        cases = [
            DocumentFilter(),
            DocumentFilter(latest_only=False),
            DocumentFilter(tenant="acme"),
            DocumentFilter(tenant="acme", latest_only=False),
            DocumentFilter(status=DocumentStatus.FAILED),
            DocumentFilter(keyword="handbook"),
            DocumentFilter(keyword="HANDBOOK"),
            DocumentFilter(keyword="book", latest_only=False),
            DocumentFilter(department="人力部"),
            DocumentFilter(doc_type="手册"),
            DocumentFilter(tenant="acme", keyword="handbook"),
            DocumentFilter(limit=2),
            DocumentFilter(limit=2, offset=1),
            DocumentFilter(limit=2, offset=2, latest_only=False),
            DocumentFilter(limit=10, offset=100),
        ]

        for filters in cases:
            pg_items = pg.list(filters)
            memory_items = memory.list(filters)
            assert [item.id for item in pg_items] == [
                item.id for item in memory_items
            ], f"筛选条件 {filters} 下两种实现返回的集合或顺序不同"
            assert pg.count(filters) == memory.count(filters), (
                f"筛选条件 {filters} 下两种实现的总数不同"
            )
    finally:
        db.close()


def test_pg_list_default_hides_superseded_and_deleted(test_pg_database):
    """默认只列现行版本:退位与已删除的版本不进清单;给参数可查历史。"""
    db = PgDatabase(test_pg_database.dsn)
    try:
        pg = PgDocumentRepository(db)
        records = _seed()
        _fill(pg, records)

        latest = pg.list(DocumentFilter())
        assert [item.file_name for item in latest] == [
            "handbook.txt",
            "notes.txt",
            "guide.txt",
            "handbook.txt",
        ]
        assert all(item.is_latest for item in latest)
        # 退位版本与已删除文档都不在默认清单里
        assert all(
            item.status not in (DocumentStatus.SUPERSEDED, DocumentStatus.DELETED)
            for item in latest
        )

        history = pg.list(DocumentFilter(latest_only=False))
        assert len(history) == 6
        assert pg.count(DocumentFilter(latest_only=False)) == 6
        # 历史里能看到退位版本与已删除文档
        assert sum(
            1 for item in history if item.status == DocumentStatus.SUPERSEDED
        ) == 1
        assert sum(
            1 for item in history if item.status == DocumentStatus.DELETED
        ) == 1
    finally:
        db.close()


def test_pg_list_sorted_by_created_at_descending_with_deterministic_tiebreak(test_pg_database):
    """创建时间倒序;同刻时按 id 兜底,次序确定。"""
    db = PgDatabase(test_pg_database.dsn)
    try:
        pg = PgDocumentRepository(db)
        same_moment = [
            _document("b.txt", minutes_ago=5),
            _document("a.txt", minutes_ago=5),
            _document("c.txt", minutes_ago=1),
        ]
        _fill(pg, same_moment)

        items = pg.list(DocumentFilter())
        assert [item.created_at for item in items] == sorted(
            [item.created_at for item in items], reverse=True
        )
        # 同刻的两条按 id 倒序,与内存实现一致
        tied = [item for item in items if item.file_name in {"a.txt", "b.txt"}]
        assert [item.id for item in tied] == sorted(
            [item.id for item in tied], reverse=True
        )
    finally:
        db.close()


def test_pg_keyword_filter_treats_wildcards_literally(test_pg_database):
    """关键字里的通配符按字面量匹配,不被当成通配符。"""
    db = PgDatabase(test_pg_database.dsn)
    try:
        pg = PgDocumentRepository(db)
        _fill(pg, [_document("report.txt"), _document("100%-done.txt")])

        assert len(pg.list(DocumentFilter(keyword="100%"))) == 1
        # "%" 单字符通配符本会匹配一切,转义后只匹配含 "%" 的文件名
        assert len(pg.list(DocumentFilter(keyword="%"))) == 1
        assert len(pg.list(DocumentFilter(keyword="zzzz"))) == 0
    finally:
        db.close()


def test_pg_list_offset_beyond_total_returns_empty_with_correct_total(test_pg_database):
    """分页越界:空清单 + 正确总数,不报错。"""
    db = PgDatabase(test_pg_database.dsn)
    try:
        pg = PgDocumentRepository(db)
        _fill(pg, _seed())

        filters = DocumentFilter(limit=10, offset=100)
        assert pg.list(filters) == []
        assert pg.count(filters) == 4
    finally:
        db.close()


# ===== 应用主接缝:pgvector 装配下清单端点走通 =====


def test_document_list_endpoint_over_pgvector(settings, test_pg_database):
    """pgvector 装配下清单端点走通:上传 → 列出 → 筛选 → 重启仍在。"""
    pg_settings = settings.model_copy(
        update={"vector_store": "pgvector", "database_url": test_pg_database.dsn}
    )

    with TestClient(
        create_app(pg_settings, embedder=DeterministicEmbedder()),
        raise_server_exceptions=False,
    ) as client:
        upload = client.post(
            DOCUMENTS_URL,
            files={"file": ("employee.md", "# 员工手册\n\n正文。\n".encode(), "text/markdown")},
            data={"tenant": "acme"},
        )
        assert upload.status_code == 201, upload.text
        document_id = upload.json()["data"]["id"]

        listed = client.get(DOCUMENTS_URL).json()["data"]
        assert listed["total"] == 1
        assert listed["items"][0]["id"] == document_id

        filtered = client.get(DOCUMENTS_URL, params={"tenant": "acme"}).json()["data"]
        assert filtered["total"] == 1
        assert client.get(DOCUMENTS_URL, params={"tenant": "other"}).json()["data"][
            "total"
        ] == 0

    # "重启容器":全新应用实例挂同库 —— 清单仍在
    with TestClient(
        create_app(pg_settings, embedder=DeterministicEmbedder()),
        raise_server_exceptions=False,
    ) as client:
        listed = client.get(DOCUMENTS_URL).json()["data"]
        assert listed["total"] == 1
        assert listed["items"][0]["id"] == document_id
