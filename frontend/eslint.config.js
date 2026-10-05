// ESLint 9 flat config：vue3-essential 起步，规则从宽。
// 历史欠债较多的规则一律先 warn（不阻塞 CI），后续再分批收紧。
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import globals from 'globals'

export default [
  {
    ignores: ['dist/**', 'node_modules/**'],
  },
  js.configs.recommended,
  ...pluginVue.configs['flat/essential'],
  {
    languageOptions: {
      globals: {
        ...globals.browser,
      },
    },
    rules: {
      // ---- error 级：真正容易出 bug 的少量核心规则 ----
      'no-undef': 'error',
      'no-constant-condition': 'error',
      'no-dupe-keys': 'error',
      'no-unreachable': 'error',
      'vue/no-dupe-keys': 'error',
      'vue/no-side-effects-in-computed-properties': 'error',
      'vue/require-v-for-key': 'error',

      // ---- warn 级：历史欠债多，先提示不阻塞 ----
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none' }],
      'no-empty': ['warn', { allowEmptyCatch: true }],
      'no-prototype-builtins': 'warn',
      'no-useless-escape': 'warn',
      'vue/multi-word-component-names': 'off', // 现有页面全是单词名（Login.vue 等），欠债主战场
      'vue/no-unused-vars': 'warn',
      'vue/no-mutating-props': 'warn',
      'vue/attributes-order': 'off',
    },
  },
]
