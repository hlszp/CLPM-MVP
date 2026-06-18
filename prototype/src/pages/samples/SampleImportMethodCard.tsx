export function SampleImportMethodCard({
  label,
  detail,
  active,
  onClick,
}: {
  label: string;
  detail: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`sample-import-card ${active ? 'active' : ''}`}
      aria-pressed={active}
      onClick={onClick}
    >
      <strong>{label}</strong>
      <span>{detail}</span>
    </button>
  );
}
