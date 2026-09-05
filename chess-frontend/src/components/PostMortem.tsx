import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import type { Square } from 'chess.js';
import { getCustomPieces } from '../pieceThemes';
import { lossText } from '../moveQuality';
import type { PieceThemeName } from '../pieceThemes';
import { useBoardSize } from '../hooks/useBoardSize';
import { useFittedBoardSize } from '../hooks/useFittedBoardSize';
import { useStacked } from '../hooks/useStacked';
import { postmortemService } from '../services/postmortemService';
import type {
    AnalysisReport,
    PostMortemChatTurn,
    PostMortemState,
} from '../types/postmortem';
import { PostMortemChat } from './PostMortemChat';
import { PostMortemDropzone } from './PostMortemDropzone';
import { PostMortemMoveList } from './PostMortemMoveList';
import { PostMortemReport } from './PostMortemReport';
import './PostMortem.css';

// Post-Mortem - the third mode. Bring a finished game, walk it, ask about it,
// and play what you wish you had played.
//
// A sibling of ChessBoard and Sandbox, not a section inside either. It owns a
// review id and nothing else: the imported game, the branches off it, every
// grade and the coach's evidence all live server-side, which is the point.
// The one piece of chess logic on this side is chess.js turning a click pair
// into a UCI string, and even that is checked again by the server before it
// changes anything.
//
// Two states, and the interface must never be ambiguous about which:
//
//   THE GAME    what was actually played. Immutable. Stepping through it is
//               the default, and `forward` follows it rather than the newest
//               branch.
//   A WHAT-IF   a line the user is trying instead. Marked in the frame, in
//               the panel and in the chat's own context line, and always one
//               click from being left.

/** Rank and file labels, shared with both other boards. */
const BOARD_NOTATION_STYLE: Record<string, string | number> = {
    color: 'var(--board-notation)',
    fontFamily: 'var(--font-mono)',
    fontWeight: 700,
    textShadow:
        '0 0 2px var(--board-notation-halo), 0 0 2px var(--board-notation-halo),'
        + ' 0 0 3px var(--board-notation-halo), 0 0 4px var(--board-notation-halo)',
};

/**
 * Which review is open, so a reload comes back to the game you were reading
 * rather than to an empty canvas. The server keeps the review for an hour of
 * idleness; a miss is a 404 and lands on the dropzone, which is what would
 * have happened anyway.
 */
const GAME_KEY = 'postmortem-game';
/** Which panel was open. Same reasoning as the sandbox's. */
const PANEL_KEY = 'postmortem-panel';
/**
 * Post-Mortem's piece set, separate from the other two modes but defaulting to
 * the game's, exactly as Learner Mode's does - a set chosen for a game you are
 * playing is not automatically the set for a game you are reading.
 */
const THEME_KEY = 'postmortem-piece-theme';
const GAME_THEME_KEY = 'chess-piece-theme';

/** How often to re-poll the whole-game scan while it is running. */
const SCAN_POLL_MS = 1200;

type Panel = 'chat' | 'moves' | 'report';

const PANELS: { id: Panel; label: string; sub: string }[] = [
    { id: 'chat', label: 'Coach', sub: 'Ask about the position on the board' },
    { id: 'moves', label: 'Moves', sub: 'The game as it was played' },
    { id: 'report', label: 'Report', sub: 'What the engine found' },
];

function stored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
    try {
        const value = localStorage.getItem(key);
        if (value && allowed.includes(value as T)) {
            return value as T;
        }
    } catch {
        // localStorage unavailable (private browsing) - use the default.
    }
    return fallback;
}

function remember(key: string, value: string): void {
    try {
        localStorage.setItem(key, value);
    } catch {
        // Not being able to remember is not a reason to fail the interaction.
    }
}

export function PostMortem() {
    const [state, setState] = useState<PostMortemState | null>(null);
    const [report, setReport] = useState<AnalysisReport | null>(null);
    const [importing, setImporting] = useState(false);
    const [importError, setImportError] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [thinking, setThinking] = useState(false);
    const [exploring, setExploring] = useState(false);
    const [selectedSquare, setSelectedSquare] = useState<string | null>(null);
    const [panel, setPanel] = useState<Panel>(() => stored(PANEL_KEY, ['chat', 'moves', 'report'] as const, 'chat'));
    const [pieceTheme] = useState<PieceThemeName>(() => {
        const allowed = ['stencil', 'bold', 'rounded', 'named'] as const;
        try {
            const own = localStorage.getItem(THEME_KEY);
            if (own && allowed.includes(own as PieceThemeName)) {
                return own as PieceThemeName;
            }
            const game = localStorage.getItem(GAME_THEME_KEY);
            if (game && allowed.includes(game as PieceThemeName)) {
                return game as PieceThemeName;
            }
        } catch {
            // Fall through to the default.
        }
        return 'stencil';
    });

    const [history, setHistory] = useState<PostMortemChatTurn[]>([]);
    const [draft, setDraft] = useState('');
    const [pending, setPending] = useState<string | null>(null);
    const [chatError, setChatError] = useState<string | null>(null);

    const boardColumnRef = useRef<HTMLDivElement>(null);
    // 360 of chrome, the sandbox's figure: this column carries an alert strip,
    // a transport row and a status line under the board, which is the same
    // shape. useFittedBoardSize then corrects by the page's actual overflow,
    // so the number never needs retuning when a row is added.
    const widthTarget = useBoardSize(360);
    // Off while the layout is stacked: there the panel below the board is what
    // makes the page tall, and correcting the board for it ran this mode's
    // board down to 236px at 1024. See hooks/useStacked.ts.
    const stacked = useStacked();
    const boardSize = useFittedBoardSize(boardColumnRef, widthTarget, !stacked);
    const customPieces = useMemo(() => getCustomPieces(pieceTheme), [pieceTheme]);

    const gameId = state?.game_id ?? null;

    useEffect(() => remember(PANEL_KEY, panel), [panel]);

    // --- opening and closing a review ---------------------------------------

    // Resume the review that was open, if the server still has it.
    useEffect(() => {
        let cancelled = false;
        const saved = (() => {
            try {
                return localStorage.getItem(GAME_KEY);
            } catch {
                return null;
            }
        })();
        if (!saved) {
            return;
        }
        (async () => {
            try {
                const resumed = await postmortemService.getGame(saved);
                if (cancelled) {
                    return;
                }
                setState(resumed);
                const transcript = await postmortemService.chatHistory(saved);
                if (!cancelled) {
                    setHistory(transcript.history);
                }
            } catch {
                // Swept, or belonging to a different identity. Either way the
                // right answer is the empty canvas, silently - the user did not
                // ask for this request and should not be told it failed.
                remember(GAME_KEY, '');
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    const openGame = useCallback(async (pgn: string, name: string) => {
        setImporting(true);
        setImportError(null);
        setError(null);
        try {
            const opened = await postmortemService.importPgn(pgn, name);
            setState(opened);
            setReport(null);
            setHistory([]);
            setChatError(null);
            setExploring(false);
            remember(GAME_KEY, opened.game_id);
            // Start the scan straight away. It takes about a minute on a full
            // game and the user is going to want the grades; waiting for them
            // to ask would mean the wait starts when they are already looking.
            void postmortemService.startScan(opened.game_id).catch(() => undefined);
        } catch (exc) {
            setImportError(exc instanceof Error ? exc.message : 'That game could not be opened.');
        } finally {
            setImporting(false);
        }
    }, []);

    const closeGame = useCallback(async () => {
        const id = gameId;
        setState(null);
        setReport(null);
        setHistory([]);
        setExploring(false);
        setError(null);
        remember(GAME_KEY, '');
        if (id) {
            // Best effort: the review is already gone from this screen, and a
            // failed delete just leaves it to the server's idle sweep.
            await postmortemService.deleteGame(id).catch(() => undefined);
        }
    }, [gameId]);

    // --- the whole-game scan -------------------------------------------------

    const refreshReport = useCallback(async (id: string) => {
        try {
            const next = await postmortemService.analysis(id);
            setReport(next);
            // The grades belong on the move list too, and the state's copy is
            // whatever it was when the last navigation happened.
            setState(current => (current && current.game_id === id
                ? { ...current, moves: next.moves, scan: next.scan, summary: next.summary }
                : current));
            return next;
        } catch {
            return null;
        }
    }, []);

    useEffect(() => {
        if (!gameId) {
            return;
        }
        let cancelled = false;
        let timer: number | undefined;

        const poll = async () => {
            const next = await refreshReport(gameId);
            if (cancelled) {
                return;
            }
            if (next && next.scan.status === 'running') {
                timer = window.setTimeout(poll, SCAN_POLL_MS);
            }
        };
        void poll();

        return () => {
            cancelled = true;
            if (timer !== undefined) {
                window.clearTimeout(timer);
            }
        };
    }, [gameId, refreshReport]);

    const startScan = useCallback(async () => {
        if (!gameId) {
            return;
        }
        try {
            await postmortemService.startScan(gameId);
            const next = await refreshReport(gameId);
            if (next?.scan.status === 'running') {
                window.setTimeout(() => void refreshReport(gameId), SCAN_POLL_MS);
            }
        } catch (exc) {
            setError(exc instanceof Error ? exc.message : 'The analysis could not be started.');
        }
    }, [gameId, refreshReport]);

    // --- navigation ----------------------------------------------------------

    const run = useCallback(async (
        action: (id: string) => Promise<PostMortemState>,
        options: { thinking?: boolean } = {},
    ) => {
        if (!gameId) {
            return;
        }
        setError(null);
        setSelectedSquare(null);
        if (options.thinking) {
            setThinking(true);
        }
        setBusy(true);
        try {
            setState(await action(gameId));
        } catch (exc) {
            const message = exc instanceof Error ? exc.message : 'That did not work.';
            setError(message);
            // A review the server no longer has is not an error to sit on: the
            // 404 says so in words, and the only way forward is a fresh import.
            if ((exc as { status?: number }).status === 404) {
                setState(null);
                remember(GAME_KEY, '');
            }
        } finally {
            setBusy(false);
            setThinking(false);
        }
    }, [gameId]);

    const goTo = useCallback((nodeId: string) => {
        void run(id => postmortemService.goto(id, nodeId));
    }, [run]);

    // Arrow keys walk the game, the same binding Learner Mode uses. Bound at
    // the document but ignoring inputs and modified keys, so Cmd/Ctrl+Left is
    // still "go back" in the browser and typing in the composer still types.
    useEffect(() => {
        if (!gameId) {
            return;
        }
        const onKey = (event: KeyboardEvent) => {
            const target = event.target as HTMLElement | null;
            if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) {
                return;
            }
            if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) {
                return;
            }
            if (event.key === 'ArrowLeft') {
                event.preventDefault();
                void run(id => postmortemService.back(id));
            } else if (event.key === 'ArrowRight') {
                event.preventDefault();
                void run(id => postmortemService.forward(id));
            }
        };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [gameId, run]);

    // --- playing a different move -------------------------------------------

    const board = useMemo(() => (state ? new Chess(state.fen) : null), [state?.fen]);

    const legalTargets = useMemo(() => {
        const map = new Map<string, Set<string>>();
        for (const uci of state?.legal_moves ?? []) {
            const from = uci.slice(0, 2);
            let targets = map.get(from);
            if (!targets) {
                targets = new Set<string>();
                map.set(from, targets);
            }
            targets.add(uci.slice(2, 4));
        }
        return map;
    }, [state]);

    const occupied = useMemo(() => {
        const set = new Set<string>();
        if (!board) {
            return set;
        }
        for (const row of board.board()) {
            for (const square of row) {
                if (square) {
                    set.add(square.square);
                }
            }
        }
        return set;
    }, [board]);

    const isOver = state?.status.state === 'checkmate'
        || state?.status.state === 'stalemate'
        || state?.status.state === 'draw';
    const interactive = exploring && !busy && !isOver && Boolean(state);

    /**
     * A click pair as a UCI string, promoting to a queen.
     *
     * The only chess chess.js does here is tell a promotion from an ordinary
     * move; legality is decided server-side, and an illegal move comes back as
     * a 400 rather than being silently swallowed. Queen because this is a
     * what-if being sketched, and stopping to ask which piece would interrupt
     * the one interaction the mode exists for. Underpromotion in a branch is a
     * gap, and a deliberate one.
     */
    const uciFor = useCallback((from: string, to: string): string | null => {
        if (!legalTargets.get(from)?.has(to)) {
            return null;
        }
        const promotion = state?.legal_moves.includes(`${from}${to}q`) ? 'q' : '';
        return `${from}${to}${promotion}`;
    }, [legalTargets, state]);

    /**
     * Play a different move, then let the engine answer it.
     *
     * The reply is automatic and at full strength: "what would have happened
     * if I had played this instead" is a question about best play, and making
     * the user click again to hear the answer puts a step between the question
     * and the point of asking it. The two are separate requests so the board
     * shows the user's move immediately and the wait is visibly the engine's.
     */
    const playAlternative = useCallback(async (uci: string) => {
        if (!gameId) {
            return;
        }
        setError(null);
        setSelectedSquare(null);
        setBusy(true);
        try {
            const branched = await postmortemService.branch(gameId, uci);
            setState(branched);
            if (!branched.on_mainline && branched.status.state === 'playing') {
                setThinking(true);
                try {
                    setState(await postmortemService.aiMove(gameId));
                } catch (exc) {
                    // The branch itself succeeded. Say the reply failed rather
                    // than rolling back a move the user just watched land.
                    setError(exc instanceof Error
                        ? `Your move is on the board, but the engine could not reply: ${exc.message}`
                        : 'Your move is on the board, but the engine could not reply.');
                } finally {
                    setThinking(false);
                }
            }
        } catch (exc) {
            setError(exc instanceof Error ? exc.message : 'That move could not be played.');
        } finally {
            setBusy(false);
        }
    }, [gameId]);

    const onSquareClick = useCallback((square: Square) => {
        if (!interactive) {
            return;
        }
        if (selectedSquare === square) {
            setSelectedSquare(null);
            return;
        }
        if (selectedSquare) {
            const uci = uciFor(selectedSquare, square);
            if (uci) {
                void playAlternative(uci);
                return;
            }
        }
        setSelectedSquare(legalTargets.has(square) ? square : null);
    }, [interactive, selectedSquare, uciFor, playAlternative, legalTargets]);

    const onPieceDrop = useCallback((from: Square, to: Square): boolean => {
        if (!interactive) {
            return false;
        }
        const uci = uciFor(from, to);
        if (!uci) {
            return false;
        }
        void playAlternative(uci);
        return true;
    }, [interactive, uciFor, playAlternative]);

    /**
     * Square hints in the app's own amber / green / red. Ice is the AI's voice
     * and a highlight under the user's own finger is the one place it would be
     * actively misleading.
     */
    const squareStyles = useMemo(() => {
        const styles: Record<string, CSSProperties> = {};
        // The move that produced this position, marked the way the real game
        // marks its last move - stepping through a game with no indication of
        // what just moved makes every position a puzzle.
        const uci = state?.node.move;
        if (uci) {
            styles[uci.slice(0, 2)] = { backgroundColor: 'var(--sq-last)' };
            styles[uci.slice(2, 4)] = { backgroundColor: 'var(--sq-last)' };
        }
        if (!interactive || !selectedSquare) {
            return styles;
        }
        styles[selectedSquare] = { backgroundColor: 'var(--sq-selected)' };
        for (const target of legalTargets.get(selectedSquare) ?? []) {
            styles[target] = {
                backgroundColor: occupied.has(target) ? 'var(--sq-capture)' : 'var(--sq-legal)',
            };
        }
        return styles;
    }, [state, interactive, selectedSquare, legalTargets, occupied]);

    // --- chat ----------------------------------------------------------------

    const sendMessage = useCallback(async () => {
        const message = draft.trim();
        if (!message || !gameId || pending) {
            return;
        }
        setDraft('');
        setPending(message);
        setChatError(null);
        try {
            const reply = await postmortemService.chat(gameId, message);
            setHistory(reply.history);
        } catch (exc) {
            setChatError(exc instanceof Error ? exc.message : 'The coach could not answer just now.');
            // The question goes back in the box rather than being lost, which
            // is what a failed send should cost: nothing.
            setDraft(message);
        } finally {
            setPending(null);
        }
    }, [draft, gameId, pending]);

    // --- render ---------------------------------------------------------------

    if (!state) {
        return (
            <div className="pm">
                <PostMortemDropzone
                    onPgn={openGame}
                    busy={importing}
                    error={importError}
                    onDismissError={() => setImportError(null)}
                />
            </div>
        );
    }

    const white = state.headers.White ?? 'White';
    const black = state.headers.Black ?? 'Black';
    const panelMeta = PANELS.find(p => p.id === panel) ?? PANELS[0];
    const analysis = state.analysis;

    const alert = (() => {
        if (state.status.state === 'checkmate') {
            return { kind: 'danger', text: `Checkmate - ${state.status.winner === 'white' ? 'White' : 'Black'} wins` };
        }
        if (state.status.state === 'stalemate') {
            return { kind: 'warn', text: 'Stalemate' };
        }
        if (state.status.state === 'draw') {
            return { kind: 'warn', text: `Draw - ${state.status.reason ?? 'no result possible'}` };
        }
        if (state.status.state === 'check') {
            return { kind: 'warn', text: 'Check' };
        }
        if (state.ply === state.total_plies && state.on_mainline) {
            const { kind, detail } = state.termination;
            return {
                kind: 'quiet',
                text: kind === 'decisive' || kind === 'draw'
                    ? `End of the game - ${state.result}${detail ? `, ${detail}` : ''}`
                    : `End of the game - ${state.result}`,
            };
        }
        return null;
    })();

    const contextLabel = state.on_mainline
        ? (state.ply === 0
            ? 'Asking about the starting position'
            : `Asking about ${Math.ceil(state.ply / 2)}${state.node.mover === 'white' ? '.' : '...'} ${state.node.san}, as played`)
        // The same move number the strip under the board uses. They were one
        // apart - "from move 9" against "instead of move 10" for the same
        // branch - which reads as two different departure points.
        : `Asking about your alternative to move ${Math.ceil(((state.branch_ply ?? 0) + 1) / 2)}`;

    return (
        <div
            className="pm"
            style={{ ['--board-size' as string]: `${boardSize}px` } as CSSProperties}
        >
            {error && <div className="pm-error fade-slide-in" role="alert">{error}</div>}

            <div className="pm-body">
                {/* The heading sits over the board's column and in its own grid
                    row, for the reason the sandbox's does: inside the right
                    column it pushes the panel down while the board starts at
                    the top, and the two columns then begin at different
                    heights. */}
                <div className="pm-identity">
                    <div className="pm-identity-row">
                        <h2 className="pm-title">
                            {white} <span className="pm-vs">vs</span> {black}
                        </h2>
                        {/* The one way out, on the layout's right edge rather
                            than the board's. Quiet: closing is never what you
                            came here to do, and it throws the game away. */}
                        <button
                            type="button"
                            className="ws-exit"
                            onClick={() => void closeGame()}
                            title="Close this review and go back to the empty canvas"
                        >
                            Close game
                        </button>
                    </div>
                    <span className="pm-subtitle">
                        {[
                            state.result !== '*' ? state.result : null,
                            state.headers.Event,
                            state.headers.Date,
                            `${state.total_plies} half-moves`,
                            state.game_count > 1 ? `first of ${state.game_count} games in ${state.source_name}` : state.source_name,
                        ].filter(Boolean).join(' · ')}
                    </span>
                </div>

                <div className="pm-board-column" ref={boardColumnRef}>
                    {/* The frame says which of the two states you are in. A
                        branch is a different colour of border and a label, not
                        a modal or a mode switch - you can still see the game's
                        move list beside it. */}
                    <div className={`pm-board-wrapper ${state.on_mainline ? '' : 'is-branch'} ${thinking ? 'is-thinking' : ''}`}>
                        <Chessboard
                            position={state.fen}
                            boardWidth={boardSize}
                            arePiecesDraggable={interactive}
                            isDraggablePiece={({ sourceSquare }) => legalTargets.has(sourceSquare)}
                            onPieceDrop={onPieceDrop}
                            onSquareClick={onSquareClick}
                            customSquareStyles={squareStyles}
                            animationDuration={300}
                            customPieces={customPieces}
                            customNotationStyle={BOARD_NOTATION_STYLE}
                            customDarkSquareStyle={{ backgroundColor: 'var(--board-dark)' }}
                            customLightSquareStyle={{ backgroundColor: 'var(--board-light)' }}
                        />
                    </div>

                    {/* One strip: where you are on the left, what the position
                        is doing on the right. Always in the DOM, so a position
                        arriving at mate does not shove every button down a row
                        as you reach for one - and because the row is shared it
                        reserves a line that is never blank, instead of a blank
                        band between the board and its own controls. */}
                    <div className="pm-strip">
                    <div className="pm-where">
                        {state.on_mainline ? (
                            <span className="pm-where-game">
                                {state.ply === 0 ? 'Start of the game' : (
                                    <>
                                        {`Move ${Math.ceil(state.ply / 2)}${state.node.mover === 'white' ? '.' : '...'} ${state.node.san}`}
                                        {/* Only where it counts something. "Start of
                                            the game of 17" is not a sentence. */}
                                        <span className="pm-where-of"> of {Math.ceil(state.total_plies / 2)}</span>
                                    </>
                                )}
                            </span>
                        ) : (
                            <span className="pm-where-branch">
                                What if: {state.branch_line_san.join(' ')}
                                <span className="pm-where-of">
                                    {' '}instead of move {Math.ceil(((state.branch_ply ?? 0) + 1) / 2)}
                                </span>
                            </span>
                        )}
                    </div>
                        <div
                            className={`pm-alert ${alert ? `is-${alert.kind}` : 'is-quiet'}`}
                            role="status"
                            aria-live="polite"
                        >
                            {alert?.text ?? ''}
                        </div>
                    </div>

                    <div className="pm-controls">
                        <button
                            className="action-btn pm-nav-btn"
                            onClick={() => void run(id => postmortemService.back(id))}
                            disabled={busy || state.ply === 0}
                            title="Back one half-move (Left arrow)"
                        >
                            Previous
                        </button>
                        <button
                            className="action-btn pm-nav-btn"
                            onClick={() => void run(id => postmortemService.forward(id))}
                            disabled={busy || (state.on_mainline && state.ply >= state.total_plies)}
                            title="Forward one half-move (Right arrow)"
                        >
                            Next
                        </button>
                        <button
                            type="button"
                            className={`action-btn pm-explore-btn ${exploring ? 'is-on' : ''}`}
                            onClick={() => {
                                setExploring(!exploring);
                                setSelectedSquare(null);
                            }}
                            disabled={busy || isOver}
                            aria-pressed={exploring}
                            title={exploring
                                ? 'Stop; the board goes back to being a replay'
                                : 'Play a legal move from this position and see what would have happened'}
                        >
                            {exploring ? 'Playing' : 'Play a different move'}
                        </button>
                        {/* Only where it means something. On the game it would
                            be a control that does nothing, which is worse than
                            one that is not there. */}
                        {!state.on_mainline && (
                            <button
                                type="button"
                                className="action-btn pm-return-btn"
                                onClick={() => void run(id => postmortemService.returnToGame(id))}
                                disabled={busy}
                                title="Back to the game as it was actually played"
                            >
                                Back to the game
                            </button>
                        )}
                    </div>

                    <div className={`pm-status ${thinking ? 'is-busy' : ''}`} role="status" aria-live="polite">
                        {thinking ? 'The engine is answering your move...' : ''}
                    </div>
                </div>

                <div className="pm-canvas">
                    <div className="pm-canvas-header">
                        <div className="pm-tabs" role="tablist" aria-label="Panel">
                            {PANELS.map(item => (
                                <button
                                    key={item.id}
                                    type="button"
                                    role="tab"
                                    aria-selected={panel === item.id}
                                    className={`pm-tab ${panel === item.id ? 'is-active' : ''}`}
                                    onClick={() => setPanel(item.id)}
                                >
                                    {item.label}
                                </button>
                            ))}
                        </div>
                        <span className="pm-canvas-sub">{panelMeta.sub}</span>
                    </div>

                    <div className="pm-canvas-inner">
                        {panel === 'chat' && (
                            <PostMortemChat
                                history={history}
                                pending={pending}
                                error={chatError}
                                draft={draft}
                                onDraft={setDraft}
                                onSend={() => void sendMessage()}
                                contextLabel={contextLabel}
                                disabled={pending !== null}
                            />
                        )}

                        {panel === 'moves' && (
                            <>
                                <PostMortemMoveList
                                    moves={state.moves}
                                    currentId={state.current_id}
                                    onSelect={goTo}
                                    disabled={busy}
                                />
                                {/* The evidence for the move on the board, under
                                    the list that selects it. Every figure here
                                    came from the engine; a field it did not
                                    produce is absent rather than estimated. */}
                                {analysis && (
                                    <div className="pm-evidence">
                                        <h3 className="pm-section-title">{analysis.san}</h3>
                                        <dl className="pm-evidence-grid">
                                            {analysis.quality && (
                                                <>
                                                    <dt>Grade</dt>
                                                    <dd>{analysis.quality.name}</dd>
                                                </>
                                            )}
                                            {lossText(analysis.cpl, analysis.quality?.label) && (
                                                <>
                                                    <dt>Cost</dt>
                                                    <dd>{lossText(analysis.cpl, analysis.quality?.label)}</dd>
                                                </>
                                            )}
                                            {analysis.best_san && (
                                                <>
                                                    <dt>Engine preferred</dt>
                                                    <dd>{analysis.best_san}</dd>
                                                </>
                                            )}
                                            {analysis.pv_san.length > 0 && (
                                                <>
                                                    <dt>Its line</dt>
                                                    <dd className="pm-evidence-line">{analysis.pv_san.join(' ')}</dd>
                                                </>
                                            )}
                                            {analysis.depth && (
                                                <>
                                                    <dt>Depth</dt>
                                                    <dd>{analysis.depth}</dd>
                                                </>
                                            )}
                                        </dl>
                                    </div>
                                )}
                            </>
                        )}

                        {panel === 'report' && (
                            <PostMortemReport
                                scan={report?.scan ?? state.scan}
                                summary={report?.summary ?? state.summary}
                                curve={report?.curve ?? []}
                                moves={report?.moves ?? state.moves}
                                currentPly={state.ply}
                                onSelect={goTo}
                                onRetry={() => void startScan()}
                            />
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
