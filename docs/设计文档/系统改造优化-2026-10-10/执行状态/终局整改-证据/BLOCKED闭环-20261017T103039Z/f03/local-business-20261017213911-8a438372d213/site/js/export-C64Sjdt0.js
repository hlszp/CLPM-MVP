function e(e){return e==null?``:String(e)}function t(e){return`"${e.replaceAll(`"`,`""`)}"`}function n(e,t){let n=URL.createObjectURL(e),r=document.createElement(`a`);r.href=n,r.download=t,r.click(),URL.revokeObjectURL(n)}function r(r,i,a){let o=[i.map(e=>t(e)).join(`,`),...a.map(n=>n.map(n=>t(e(n))).join(`,`))].join(`
`);n(new Blob([`\uFEFF${o}`],{type:`text/csv;charset=utf-8;`}),`${r}.csv`)}function i(t,r,i,o=`数据`){let s=`<tr>${r.map(e=>`<th style="background:#f0f0f0;font-weight:bold;">${a(e)}</th>`).join(``)}</tr>`,c=i.map(t=>`<tr>${t.map(t=>{let n=e(t);return`<td${n!==``&&!Number.isNaN(Number(n))?String.raw` style="mso-number-format:\@;"`:``}>${a(n)}</td>`}).join(``)}</tr>`).join(`
`),l=`<!DOCTYPE html>
<html xmlns:o="urn:schemas-microsoft-com:office:office"
      xmlns:x="urn:schemas-microsoft-com:office:excel"
      xmlns="http://www.w3.org/TR/REC-html40">
<head>
  <meta charset="UTF-8">
  <!--[if gte mso 9]>
  <xml><x:ExcelWorkbook><x:ExcelWorksheets>
    <x:ExcelWorksheet><x:Name>${a(o)}</x:Name>
    <x:WorksheetOptions><x:DisplayGridlines/></x:WorksheetOptions>
    </x:ExcelWorksheet>
  </x:ExcelWorksheets></x:ExcelWorkbook></xml>
  <![endif]-->
</head>
<body><table border="1">${s}${c}</table></body>
</html>`;n(new Blob([l],{type:`application/vnd.ms-excel;charset=utf-8;`}),`${t}.xls`)}function a(e){return e.replaceAll(`&`,`&amp;`).replaceAll(`<`,`&lt;`).replaceAll(`>`,`&gt;`).replaceAll(`"`,`&quot;`).replaceAll(`'`,`&#39;`)}function o(e){let{filename:t,format:n=`csv`,headers:a,rows:o,sheetName:s=`数据`}=e;n===`excel`?i(t,a,o,s):r(t,a,o)}export{o as t};