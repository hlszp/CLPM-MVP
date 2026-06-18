import { MetricCard } from '../../components/ui';

interface PerformanceOverviewBoardProps {
  cards: ReadonlyArray<{ key: string; label: string; value: string; delta: string }>;
}

export function PerformanceOverviewBoard({ cards }: PerformanceOverviewBoardProps) {
  return (
    <section className="grid four" aria-label="绩效总览板">
      {cards.map((card) => (
        <MetricCard key={card.key} label={card.label} value={card.value} delta={card.delta} />
      ))}
    </section>
  );
}
