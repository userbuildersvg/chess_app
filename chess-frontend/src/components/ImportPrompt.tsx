import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { accountService } from '../services/accountService';
import { authService } from '../services/authService';
import { learningService } from '../services/learningService';
import type { ExternalImportResult } from '../services/profileService';
import { ExternalImport } from './ExternalImport';
import './ExternalImport.css';

/**
 * "Import your recent games" - shown once, after an account is created or
 * first signed into on this build.
 *
 * WHEN IT APPEARS
 * ---------------
 * The account pages end in a reload of `/`, so the prompt lives in App and
 * decides for itself: signed in, and the account's `importPromptSeen`
 * preference is still false. The preference is on the ACCOUNT rather than in
 * localStorage so a skip on one device is a skip everywhere, and so it can
 * never come back after the person has answered it. The tool itself stays
 * reachable from Account settings, which is the "clear way to open it later".
 *
 * Skippable, always. It blocks nothing but the moment it is on screen.
 */
export function ImportPrompt() {
    const [open, setOpen] = useState(false);
    const [done, setDone] = useState<ExternalImportResult | null>(null);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const me = await authService.me();
                if (!me.signed_in || cancelled) return;
                const prefs = await accountService.prefs();
                if (cancelled || prefs.importPromptSeen) return;
                setOpen(true);
                learningService.event('external_import_prompt_viewed', { source_mode: 'Profile' });
            } catch {
                // Not signed in, or the account area is unreachable: no prompt.
            }
        })();
        return () => { cancelled = true; };
    }, []);

    const dismiss = () => {
        setOpen(false);
        void accountService.savePrefs({ importPromptSeen: true }).catch(() => undefined);
    };

    useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') dismiss(); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [open]);

    if (!open) return null;

    return (
        <div className="xi-prompt-backdrop" data-testid="import-prompt">
            <div className="xi-prompt" role="dialog" aria-modal="true" aria-labelledby="xi-prompt-title">
                {done ? (
                    <>
                        <h2 className="xi-prompt-title" id="xi-prompt-title">Games imported</h2>
                        <p className="xi-prompt-sub">
                            {done.message}. They are stored in your account by source, and each one can be
                            opened in Review from Account settings. Analysis runs in the background.
                        </p>
                        <div className="xi-actions">
                            <Link className="acct-btn acct-btn-primary" to="/settings#imported-games" onClick={dismiss}>
                                See imported games
                            </Link>
                            <button type="button" className="acct-btn acct-btn-quiet" onClick={dismiss}>
                                Back to the board
                            </button>
                        </div>
                    </>
                ) : (
                    <>
                        <h2 className="xi-prompt-title" id="xi-prompt-title">Import your recent games</h2>
                        <p className="xi-prompt-sub">
                            Zugzwang learns best from your real games. Enter your Chess.com or Lichess
                            username to import recent public games and start building your improvement
                            profile.
                        </p>
                        <ExternalImport
                            compact
                            onImported={(result) => {
                                setDone(result);
                                void accountService.savePrefs({ importPromptSeen: true }).catch(() => undefined);
                            }}
                        />
                        <div className="xi-prompt-foot">
                            <span className="xi-note">You can do this later from Account settings.</span>
                            <button type="button" className="xi-skip" onClick={dismiss}>Skip for now</button>
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
