import { useCallback, useRef, useState } from 'react';

/**
 * The empty Post-Mortem canvas: where you hand the app one of your games.
 *
 * It is the whole mode until a game is loaded, so it has one job and states
 * it once - drop a PGN here, or click to pick one. No feature tour, no sample
 * gallery, no three-step explainer. A workspace that has not been given
 * anything yet should look like a workspace, not like a product page.
 *
 * Three details are load-bearing rather than decorative:
 *
 * 1. **The whole panel is the target, and it is a button.** A small dashed
 *    rectangle in the middle of a large empty area is a smaller target than
 *    the space it sits in, and people drop on the space. Being a real
 *    <button> is also what makes the picker reachable from the keyboard,
 *    which a div with an onClick is not.
 * 2. **Drag state is counted, not toggled.** `dragenter`/`dragleave` fire for
 *    every child element the pointer crosses, so a boolean flickers off the
 *    moment the cursor passes over the label inside the target. A depth
 *    counter is the standard fix and the reason the highlight stays put.
 * 3. **The file is read here and posted as text.** The frontend never parses
 *    a PGN - reading the bytes is the whole of its involvement, and the
 *    server decides whether it is a game.
 */
export function PostMortemDropzone({
    onPgn,
    busy,
    error,
    onDismissError,
}: {
    onPgn: (pgn: string, name: string) => void;
    busy: boolean;
    error: string | null;
    onDismissError: () => void;
}) {
    const [dragging, setDragging] = useState(false);
    const [readError, setReadError] = useState<string | null>(null);
    const depth = useRef(0);
    const input = useRef<HTMLInputElement>(null);

    const take = useCallback((file: File | undefined) => {
        setReadError(null);
        if (!file) {
            return;
        }
        // A gentle nudge rather than a refusal: the extension is a hint, and
        // people do rename files. The server decides what is a game.
        if (file.size > 512 * 1024) {
            setReadError('That file is much larger than a single chess game. Try exporting one game on its own.');
            return;
        }
        const reader = new FileReader();
        reader.onerror = () => setReadError('That file could not be read from your computer.');
        reader.onload = () => onPgn(String(reader.result ?? ''), file.name);
        reader.readAsText(file);
    }, [onPgn]);

    const onDrop = useCallback((event: React.DragEvent) => {
        event.preventDefault();
        depth.current = 0;
        setDragging(false);
        take(event.dataTransfer?.files?.[0]);
    }, [take]);

    const shown = error ?? readError;

    return (
        <div className="pm-empty">
            <button
                type="button"
                className={`pm-drop ${dragging ? 'is-over' : ''} ${busy ? 'is-busy' : ''}`}
                onClick={() => input.current?.click()}
                // preventDefault on dragover is what makes an element a drop
                // target at all; without it the browser navigates to the file.
                onDragOver={event => event.preventDefault()}
                onDragEnter={event => {
                    event.preventDefault();
                    depth.current += 1;
                    setDragging(true);
                }}
                onDragLeave={() => {
                    depth.current = Math.max(0, depth.current - 1);
                    if (depth.current === 0) {
                        setDragging(false);
                    }
                }}
                onDrop={onDrop}
                disabled={busy}
                aria-busy={busy}
            >
                <span className="pm-drop-mark" aria-hidden="true">
                    {/* A board fragment rather than a cloud-with-an-arrow: this
                        is a chess workspace, and the icon should say so. */}
                    <svg viewBox="0 0 48 48" width="48" height="48">
                        <rect x="0" y="0" width="24" height="24" rx="2" fill="var(--board-dark)" />
                        <rect x="24" y="24" width="24" height="24" rx="2" fill="var(--board-dark)" />
                        <rect x="24" y="0" width="24" height="24" rx="2" fill="var(--board-light)" />
                        <rect x="0" y="24" width="24" height="24" rx="2" fill="var(--board-light)" />
                    </svg>
                </span>
                <span className="pm-drop-title">
                    {busy ? 'Importing game…' : 'Analyze a game you already played'}
                </span>
                <span className="pm-drop-body">
                    {busy
                        ? 'Replaying every move safely. Engine analysis starts next.'
                        : 'Find the decision that mattered, understand what you were trying to do, and practise the better idea.'}
                </span>
                {/* The visible affordance. The whole zone is the button (a
                    drop needs the space, not the outline), so this is a span
                    drawn as the primary action rather than a second button
                    nested inside the first. */}
                <span className="pm-drop-cta" aria-hidden="true">
                    {busy ? 'Importing…' : 'Choose a game file'}
                </span>
                <span className="pm-drop-note">{busy ? '\u00a0' : 'or drag a PGN here'}</span>
                <span className="pm-drop-fine">Upload one chess game at a time.</span>
            </button>

            <input
                ref={input}
                type="file"
                accept=".pgn,application/x-chess-pgn,text/plain"
                className="pm-file-input"
                onChange={event => {
                    take(event.target.files?.[0]);
                    // Cleared so choosing the same file twice in a row still
                    // fires a change event - otherwise a failed import cannot
                    // be retried with the same file.
                    event.target.value = '';
                }}
            />

            {shown && (
                <div className="pm-drop-error fade-slide-in" role="alert">
                    <p className="pm-drop-error-text">{shown}</p>
                    <button
                        type="button"
                        className="action-btn pm-drop-dismiss"
                        onClick={() => {
                            setReadError(null);
                            onDismissError();
                        }}
                    >
                        Try another file
                    </button>
                </div>
            )}

            <p className="pm-empty-promise">
                We’ll find the decision that mattered most, explain what you were trying to
                do, and turn it into a practice lesson.
            </p>

            {/* The one-line privacy claim, and the full account behind a
                disclosure. The full text is the load-bearing one (CLAUDE.md
                §14: a claim about someone's data is checked against the code,
                not written from intent) - it is not shortened, only folded:
                the coach is Gemini, and the review chat endpoint sends it the
                FEN, the line in SAN, the branch and the PGN's White/Black
                headers (postmortem_api.chat). */}
            <p className="pm-empty-hint">Your game stays private while you review it. Nothing is published.</p>
            <details className="pm-privacy">
                <summary>How privacy works</summary>
                <p>
                    Your game is held on the server only while you are reviewing it, and is
                    dropped after an hour idle. Nothing is published, and nothing is added to
                    your play history. Asking the coach a question sends that position and the
                    moves around it to Google's Gemini API, which is where its answers come from.
                </p>
            </details>
        </div>
    );
}
