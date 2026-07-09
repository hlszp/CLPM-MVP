import { defineConfig } from '@vben/eslint-config';

export default defineConfig([
  {
    files: ['**/*.?([cm])[jt]s?(x)'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },
]);
