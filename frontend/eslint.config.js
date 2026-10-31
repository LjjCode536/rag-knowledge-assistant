import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import globals from 'globals'
import tseslint from 'typescript-eslint'

import vueParser from 'vue-eslint-parser'

/**
 * ESLint 扁平配置:typecheck 之外的第二道质量门槛。
 *
 * 与后端的 `ruff check .` 对等:零 error 才算通过(脚本里用
 * `--max-warnings 0` 把 warning 也当失败)。
 */
export default tseslint.config(
  {
    ignores: [
      'dist/**',
      'coverage/**',
      'node_modules/**',
      'auto-imports.d.ts',
      'components.d.ts',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    // .vue 单文件组件:脚本块走 TypeScript 解析器
    files: ['**/*.vue'],
    languageOptions: {
      parser: vueParser,
      parserOptions: {
        parser: tseslint.parser,
        extraFileExtensions: ['.vue'],
        sourceType: 'module',
      },
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    rules: {
      // 组件名用多词,避免与 HTML 元素冲突(Element Plus 组件同理)
      'vue/multi-word-component-names': 'off',
      // 界面文案是中文,单字母变量名只在极小作用域内允许
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
  {
    files: ['**/*.ts'],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
  },
  {
    files: ['**/*.spec.ts'],
    languageOptions: {
      globals: {
        ...globals.node,
      },
    },
  },
)
