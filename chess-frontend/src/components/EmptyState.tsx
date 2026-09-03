import type { ReactNode } from 'react';
import './EmptyState.css';

/**
 * The panel state for "there is nothing here yet".
 *
 * An empty panel is a moment to give direction, not an apology, so these read
 * as an invitation: a short title naming the situation and one line saying
 * what will fill it. No italics - italic body text was doing the work of
 * signalling "this is placeholder", which colour and position already do, and
 * it slowed down the one sentence a new user most needs to read.
 *
 * `tone` distinguishes waiting-for-you from something the AI is doing:
 *   'idle'     - nothing has happened yet; neutral.
 *   'thinking' - work is in flight. Amber, with a live region so a screen
 *                reader is told rather than left watching a still panel.
 */
export function EmptyState({
    title,
    children,
    tone = 'idle',
    action,
}: {
    title: string;
    children?: ReactNode;
    tone?: 'idle' | 'thinking';
    action?: ReactNode;
}) {
    return (
        <div
            className={`empty-state empty-state-${tone}`}
            role={tone === 'thinking' ? 'status' : undefined}
            aria-live={tone === 'thinking' ? 'polite' : undefined}
        >
            {tone === 'thinking' && (
                <span className="empty-state-dots" aria-hidden="true">
                    <span /><span /><span />
                </span>
            )}
            <p className="empty-state-title">{title}</p>
            {children && <p className="empty-state-body">{children}</p>}
            {action && <div className="empty-state-action">{action}</div>}
        </div>
    );
}
