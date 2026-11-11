import styles from './ThinkingLoader.module.scss';

export function ThinkingLoader() {
  return (
    <div className={styles.loader} role="status">
      <span>Думаю</span>

      <div className={styles.dots} aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
    </div>
  );
}
