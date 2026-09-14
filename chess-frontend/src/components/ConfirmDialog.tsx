import { useEffect } from 'react';
import './ExternalImport.css';

/**
 * "Are you sure?" - one themed confirmation, used before removing an
 * imported game. Reuses the import prompt's modal styling rather than
 * adding a second dialog vocabulary. Escape cancels; nothing is done until
 * the confirm button is pressed.
 */
/** The copy shown before an imported game is removed. */
export function RemoveGameBody() {
    return (
        <>
            <p>This will remove this imported PGN from your account history. Any profile evidence from this game will no longer count toward your improvement profile.</p>
            <p><strong>This cannot be undone.</strong></p>
        </>
    );
}

export function ConfirmDialog({ open, title, body, confirmLabel, busy, onConfirm, onCancel }: {
    open: boolean;
    title: string;
    body: React.ReactNode;
    confirmLabel: string;
    busy?: boolean;
    onConfirm: () => void;
    onCancel: () => void;
}) {
    useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel(); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [open, onCancel]);

    if (!open) return null;
    return (
        <div className="xi-prompt-backdrop" data-testid="confirm-dialog">
            <div className="xi-prompt" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
                <h2 className="xi-prompt-title" id="confirm-title">{title}</h2>
                <div className="xi-prompt-sub">{body}</div>
                <div className="xi-actions">
                    <button type="button" className="acct-btn acct-btn-quiet" onClick={onCancel} disabled={busy} autoFocus>
                        Cancel
                    </button>
                    <button type="button" className="acct-btn acct-btn-primary" onClick={onConfirm} disabled={busy} data-testid="confirm-yes">
                        {busy ? 'Removing…' : confirmLabel}
                    </button>
                </div>
            </div>
        </div>
    );
}
