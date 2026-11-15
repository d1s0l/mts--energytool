import { useState } from 'react';

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
  const [copyStatus, setCopyStatus] = useState<{ key: string; message: string } | null>(null);

  if (!metadata) {
    return <p className={styles.empty}>Отладочные данные отсутствуют</p>;
  }

  const parsedResponse =
    metadata.parsed_response === undefined
      ? null
      : JSON.stringify(metadata.parsed_response, null, 2);
  const hasValidationErrors = metadata.validation_errors !== undefined &&
    metadata.validation_errors !== null;
  const validationErrors =
    metadata.validation_errors === undefined || metadata.validation_errors === null
      ? null
      : metadata.validation_errors.length > 0
        ? JSON.stringify(metadata.validation_errors, null, 2)
        : null;
  const rawResponses =
    metadata.raw_response_attempts && metadata.raw_response_attempts.length > 1
      ? metadata.raw_response_attempts
      : metadata.raw_response === null || metadata.raw_response === undefined
        ? []
        : [metadata.raw_response];
  const blocks = [
    {
      key: 'raw',
      title: 'Raw response',
      content:
        rawResponses.length > 1
          ? rawResponses
              .map((response, index) => `Attempt ${index + 1}\n${response}`)
              .join('\n\n')
          : rawResponses[0] ?? null,
      empty: 'Сырой ответ отсутствует',
    },
    {
      key: 'parsed',
      title: 'Parsed JSON',
      content: parsedResponse ?? null,
      empty:
        metadata.validation_errors?.length
          ? 'JSON не удалось разобрать'
          : 'Распарсенный JSON отсутствует',
    },
    {
      key: 'validation',
      title: 'Validation errors',
      content: validationErrors,
      empty: hasValidationErrors
        ? 'Ошибки валидации отсутствуют'
        : 'Данные валидации не сохранены',
    },
    {
      key: 'final',
      title: 'Final backend response',
      content: metadata.final_response ?? null,
      empty: 'Итоговый ответ отсутствует',
    },
  ];

  const handleCopy = async (key: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopyStatus({ key, message: 'Скопировано' });
    } catch {
      setCopyStatus({ key, message: 'Не удалось скопировать' });
    }
  };

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

      <h4 className={styles.sourcesTitle}>Raw LLM Response</h4>
      <div className={styles.rawBlocks}>
        {blocks.map((block) => (
          <details className={styles.rawBlock} key={block.key}>
            <summary>{block.title}</summary>
            <div className={styles.rawContent}>
              <pre>{block.content ?? block.empty}</pre>
              <div className={styles.copyRow}>
                <button
                  type="button"
                  disabled={block.content === null}
                  onClick={() => {
                    if (block.content !== null) {
                      void handleCopy(block.key, block.content);
                    }
                  }}
                >
                  Копировать
                </button>
                {copyStatus?.key === block.key && (
                  <span role="status">{copyStatus.message}</span>
                )}
              </div>
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}
