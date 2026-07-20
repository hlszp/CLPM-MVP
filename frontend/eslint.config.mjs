import { defineConfig } from '@vben/eslint-config';

export default defineConfig([
  {
    files: ['**/*.?([cm])[jt]s?(x)'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },
  {
    files: ['**/*.vue'],
    rules: {
      // Oxfmt owns Vue template layout; keep ESLint focused on semantic rules.
      'vue/html-closing-bracket-newline': 'off',
      'vue/multiline-html-element-content-newline': 'off',
    },
  },
]);
