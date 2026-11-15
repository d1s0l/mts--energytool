import type { DebugMetadata } from '@/entities/message/model/types';
import styles from './DebugPanel.module.scss';

interface DebugPanelProps {
  metadata: DebugMetadata | null;
}

function formatDuration(value: number | null): string {
  if (value === null) return 'N/A';
  return value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(2)} s`;
}

export function DebugPanel({ metadata }: DebugPanelProps) {
  if (!metadata) {
    return <p className={styles.empty}>Отладочные данные отсутствуют</p>;
  }

  return (
    <section className={styles.panel} aria-label="Отладочная информация">
      <h3 className={styles.title}>Отладочная информация</h3>

      <div className={styles.metrics}>
        <div className={styles.metric}>
          <span>Total time</span>
          <strong>{formatDuration(metadata.total_ms)}</strong>
        </div>
        <div className={styles.metric}>
          <span>Search time</span>
          <strong>{formatDuration(metadata.search_ms)}</strong>
        </div>
        <div className={styles.metric}>
          <span>Generation time</span>
          <strong>{formatDuration(metadata.generation_ms)}</strong>
        </div>
      </div>

      <h4 className={styles.sourcesTitle}>Источники базы знаний</h4>
      {metadata.sources.length === 0 ? (
        <p className={styles.empty}>Источники не найдены</p>
      ) : (
        <div className={styles.sources}>
          {metadata.sources.map((source) => (
            <details className={styles.source} key={`${source.document_id}-${source.fragment_id}`}>
              <summary>
                <span className={styles.sourceName}>{source.title}</span>
                <span className={styles.score}>Score: {source.score ?? 'N/A'}</span>
              </summary>
              <dl className={styles.sourceDetails}>
                <dt>Document ID</dt>
                <dd>{source.document_id}</dd>
                <dt>Fragment ID</dt>
                <dd>{source.fragment_id}</dd>
                <dt>Fragment</dt>
                <dd className={styles.fragment}>{source.fragment}</dd>
              </dl>
            </details>
          ))}
        </div>
      )}
    </section>
  );
}
