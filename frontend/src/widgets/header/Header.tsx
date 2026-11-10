import { ThemeSwitcher } from "@/features/change-theme/ui/ThemeSwitcher";
import styles from './Header.module.scss'
import { Link } from "react-router";

export function Header() {
    return(
        <header className={styles.header}>
            <div>
                <h1>Enegytool</h1>
            </div>

            <div className={styles.button__block}>
                
                <Link className={styles.debug_mode} to='/'>Debug mode</Link>
                
                <ThemeSwitcher />
            </div>
        </header>
    )
}