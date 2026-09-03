import { useTheme } from '../hooks/useTheme';
import './ThemeToggle.css';

/**
 * Light/dark switch.
 *
 * Icon-only is defensible here where it is not elsewhere in the app: sun and
 * moon are among the few genuinely universal UI glyphs. It still carries an
 * aria-label and a title, and the label names the DESTINATION ("Switch to
 * light") rather than the current state, because a control is named by what
 * it does when pressed.
 *
 * aria-pressed is deliberately absent. This is not a toggle between on and
 * off; it moves between two equal states, and a screen reader announcing
 * "pressed" for dark mode would imply dark is the "on" one.
 */
export function ThemeToggle() {
    const { resolved, toggle } = useTheme();
    const target = resolved === 'dark' ? 'light' : 'dark';
    const label = `Switch to ${target} theme`;

    return (
        <button
            type="button"
            className="theme-toggle"
            onClick={toggle}
            aria-label={label}
            title={label}
        >
            {/* Both icons are always in the DOM and cross-fade, so the button
                never changes size mid-press and there is no layout shift. */}
            <span className="theme-toggle-icons" aria-hidden="true">
                <svg className="theme-toggle-icon theme-toggle-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                    <circle cx="12" cy="12" r="4.2" />
                    <path d="M12 2.6v2.2M12 19.2v2.2M2.6 12h2.2M19.2 12h2.2M5.4 5.4l1.6 1.6M17 17l1.6 1.6M18.6 5.4L17 7M7 17l-1.6 1.6" />
                </svg>
                <svg className="theme-toggle-icon theme-toggle-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M20.2 14.2A8.4 8.4 0 0 1 9.8 3.8a8.4 8.4 0 1 0 10.4 10.4z" />
                </svg>
            </span>
        </button>
    );
}
