import type { MappingFieldStatus } from '../../types';

export function SampleFieldMappingEditor({ fields }: { fields: MappingFieldStatus[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>源字段</th>
            <th>目标对象</th>
            <th>覆盖率</th>
            <th>状态</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((field) => (
            <tr key={field.source}>
              <th scope="row">{field.source}</th>
              <td>{field.target}</td>
              <td>{field.coverage}</td>
              <td>{field.status}</td>
              <td>{field.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
