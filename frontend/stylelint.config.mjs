export default {
  extends: ['@vben/stylelint-config'],
  root: true,
  rules: {
    // Tailwind 工具类（如 .bg-white\/80）不遵循 BEM 命名
    'selector-class-pattern': null,
  },
};
