import { Link } from 'react-router-dom';
import { useAppSession } from '../../app/session/AppSessionContext';
import { SampleFieldMappingEditor } from './SampleFieldMappingEditor';
import { SampleImportMethodCard } from './SampleImportMethodCard';
import { getSampleImportViewModel } from './sampleReadinessModel';

export function SampleImportWizard() {
  const { sampleReadiness, setImportMethod } = useAppSession();
  const { methods, mappingMatrix, mappingGaps, dataLineage } = getSampleImportViewModel();
  const currentMethod = methods.find((method) => method.id === sampleReadiness.importMethod);

  return (
    <section className="sample-import-wizard">
      <div className="sample-step-rail" aria-label="样本导入步骤">
        <span className="active">1. 选择导入方式</span>
        <span>2. 校对字段映射</span>
        <span>3. 查看解析结果</span>
      </div>
      <div className="grid two">
        <section className="panel">
          <h2>导入方式</h2>
          <p>当前导入方式：{currentMethod?.label}</p>
          <div className="sample-import-methods">
            {methods.map((method) => (
              <SampleImportMethodCard
                key={method.id}
                label={method.label}
                detail={method.detail}
                active={sampleReadiness.importMethod === method.id}
                onClick={() => setImportMethod(method.id)}
              />
            ))}
          </div>
        </section>
        <section className="panel">
          <h2>解析结果</h2>
          <p>
            当前已接入 {dataLineage.csvFile}，采样间隔 {dataLineage.sampleIntervalSeconds}s。
          </p>
          <p>安全边界：{dataLineage.safetyBoundary}</p>
          <SampleFieldMappingEditor fields={mappingMatrix} />
          <ul>
            {mappingGaps.map((gap) => (
              <li key={`${gap.field}-${gap.scope}`}>
                {gap.field} · {gap.action}
              </li>
            ))}
          </ul>
          <Link className="button" to="/samples/readiness">
            进入就绪校验
          </Link>
        </section>
      </div>
    </section>
  );
}
