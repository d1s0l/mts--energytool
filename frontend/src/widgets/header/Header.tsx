import { ThemeSwitcher } from "@/features/change-theme/ui/ThemeSwitcher";
import styles from './Header.module.scss'
import { DebugButton } from "@/features/debug-mode-button/ui/DebugButton";

export function Header() {
    return(
        <header className={styles.header}>
            <div className={styles.logo__block}>
                <img src='/public/favicon.svg' className={styles.logo}/>
                <h1>Enegytool</h1>
            </div>

            <div className={styles.button__block}>
                <DebugButton />
                <ThemeSwitcher />
            </div>
        </header>
    )
}