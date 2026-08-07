import { defineConfig } from 'oxlint';

export default defineConfig({
  rules: {
    // 项目中存在大量历史非空断言（Vue 3 ref 模板引用等），
    // 逐个清理风险高且收益低，暂时关闭不阻塞 CI
    'typescript/no-non-null-assertion': 'off',
  },
});
