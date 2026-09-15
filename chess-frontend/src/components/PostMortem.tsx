import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import type { Square } from 'chess.js';
import { getCustomPieces } from '../pieceThemes';
import { lossText } from '../moveQuality';
import type { PieceThemeName } from '../pieceThemes';
import { useBoardSizing } from '../hooks/useBoardScale';
import { BoardSizeControl } from './BoardSizeControl';
import { CoachStyleSettings } from './CoachStyleSettings';
import { PromotionPicker } from './PromotionPicker';
import { isPromotionMove, moverColor } from './promotion';
import type { PendingPromotion, PromotionPiece } from './promotion';
import { postmortemService } from '../services/postmortemService';
import { learningService } from '../services/learningService';
import type {
    AnalysisReport,
    PostMortemChatTurn,
    PostMortemState,
} from '../types/postmortem';
import { PostMortemChat } from './PostMortemChat';
import { CorrectionPanel } from './CorrectionPanel';
import { BoardEndState } from './BoardEndState';
import { readBoardStatus } from '../boardState';
import { PostMortemDropzone } from './PostMortemDropzone';
import { PostMortemMoveList } from './PostMortemMoveList';
import { PostMortemReport } from './PostMortemReport';
import './PostMortem.css';
import { Link } from 'react-router-dom';
import '../pages/profile.css';

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

/**
 * How often to re-poll the whole-game scan while it is running.
 *
 * Ramped, like Play's move poll. The scan of a 30-ply game now finishes in
 * about a second and a half (CLAUDE.md §36 - it used to be seven, most of it
 * database writes rather than engine), so a flat 1200ms poll would show the
 * finished report up to 1.2s after it existed. Fast while it is plausibly
 * about to finish, then backing off for a long game.
 */
const SCAN_POLL_MS = 1200;
const scanPollDelay = (n: number) => (n < 8 ? 250 : n < 16 ? 600 : SCAN_POLL_MS);

type Panel = 'chat' | 'moves' | 'report' | 'correction' | 'actions';

// `Correct` is a verb here, matching `Play` / `Learn` / `Review` in the header:
// the tab is named after what you do in it, not after the object it produces
// ("Correction Cards" is the internal name and stays in the code). It sits
// last because it is where the other three lead - you find a decision in
// Report or Moves, and then you work on it.
const PANELS: { id: Panel; label: string; sub: string }[] = [
    // "Chat", the same name the other two modes give the conversation with
    // the coach. It was labelled "Coach" here, which made one thing look like
    // two across the mode switch.
    { id: 'chat', label: 'Chat', sub: 'Ask the coach about the position on the board' },
    { id: 'moves', label: 'Moves', sub: 'The game as it was played' },
    { id: 'report', label: 'Report', sub: 'What the engine found' },
    { id: 'correction', label: 'Correct', sub: 'Work through this decision and practise it' },
    { id: 'actions', label: 'Actions', sub: 'Things you reach for now and then' },
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

interface PostMortemProps {
    /**
     * A review Play just opened on the server ("Review this game"). The id is
     * enough: the game and its scan are already there. `nonce` changes per
     * handoff so two games in a row both land.
     */
    handoff?: { gameId: string; playerColor: 'white' | 'black'; nonce: number } | null;
    /** The way back when the handoff fails - Play still has the game. */
    onBackToPlay?: () => void;
}

export function PostMortem({ handoff = null, onBackToPlay }: PostMortemProps = {}) {
    const [state, setState] = useState<PostMortemState | null>(null);
    // The landing state between Play and the review: the game is on the
    // server and analysing, this screen is fetching it. Shown instead of the
    // dropzone so it never looks as though a PGN is wanted.
    const [opening, setOpening] = useState(false);
    const [openingError, setOpeningError] = useState<string | null>(null);
    const [report, setReport] = useState<AnalysisReport | null>(null);
    const [importing, setImporting] = useState(false);
    const [importError, setImportError] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [thinking, setThinking] = useState(false);
    const [activity, setActivity] = useState<string | null>(null);
    const [exploring, setExploring] = useState(false);
    const [selectedSquare, setSelectedSquare] = useState<string | null>(null);
    // The last move played into a branch. Only the Correct tab reads it, to
    // notice that the player actually made the engine's move rather than
    // reading about it.
    const [lastBranchUci, setLastBranchUci] = useState<string | null>(null);
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
    // Preference, ceiling, fit and shrink in one call - the growing and
    // shrinking halves are not symmetrical and the reasoning lives in one
    // place rather than three. See hooks/useBoardScale.
    //
    // 180, the same ceiling Play passes, not the 360 this had: Review's
    // column carries the same furniture as Play's (two seats, an alert
    // strip, a transport, a status line), and a higher figure only capped
    // the ambition the fitter was then allowed to reach. With the profile
    // CTA moved off the page bottom (see the Actions tab), the three modes
    // now draw the same board at the same setting - CLAUDE.md §35.
    const { pref: boardSizePref, setPref: setBoardSizePref, boardSize } =
        useBoardSizing(boardColumnRef, 180);

    /**
     * Which way round the board is drawn.
     *
     * Review had no rotation at all: an imported game was always seen from
     * White's side, so a player reviewing their own game as Black read every
     * position upside down - which is most of the point of importing it. It
     * is remembered per review rather than globally, because it is a fact
     * about THIS game ("I was Black in this one"), not a standing preference.
     */
    const [orientation, setOrientation] = useState<'white' | 'black'>('white');
    // Off while the layout is stacked: there the panel below the board is what
    // makes the page tall, and correcting the board for it ran this mode's
    // board down to 236px at 1024. See hooks/useStacked.ts.

    const customPieces = useMemo(() => getCustomPieces(pieceTheme), [pieceTheme]);

    const gameId = state?.game_id ?? null;

    useEffect(() => remember(PANEL_KEY, panel), [panel]);

    // --- opening and closing a review ---------------------------------------

    // A game handed over from Play. Runs before the resume effect below can
    // matter: it fires on the same mount when Review is opened by the button,
    // and on a later render when Review was already open on something else.
    const handoffNonce = handoff?.nonce ?? null;
    // For the mount-only resume effect below, which must not re-run when the
    // handoff changes but does need to know whether one is pending.
    const handoffRef = useRef(handoff);
    handoffRef.current = handoff;
    useEffect(() => {
        if (!handoff) {
            return;
        }
        let cancelled = false;
        const { gameId, playerColor } = handoff;
        setOpening(true);
        setOpeningError(null);
        setImportError(null);
        setError(null);
        (async () => {
            try {
                const opened = await postmortemService.getGame(gameId);
                if (cancelled) {
                    return;
                }
                setState(opened);
                setReport(null);
                setHistory([]);
                setChatError(null);
                setExploring(false);
                // Your game, from your side of the board.
                setOrientation(playerColor);
                remember(GAME_KEY, opened.game_id);
                // Idempotent: the server started it already; this only
                // matters if that start was lost, and it costs nothing.
                void postmortemService.startScan(opened.game_id).catch(() => undefined);
            } catch (exc) {
                if (!cancelled) {
                    setOpeningError(exc instanceof Error ? exc.message : 'The review could not be opened.');
                }
            } finally {
                if (!cancelled) {
                    setOpening(false);
                }
            }
        })();
        return () => {
            cancelled = true;
        };
        // Keyed on the nonce alone: the handoff object is rebuilt by App on
        // every render, and re-running this for the same nonce would refetch
        // the same game and reset the panel under the reader.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [handoffNonce]);

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
        // A handoff on this same mount is about to load its own game, and
        // the saved id is either that game or an older one it replaces.
        if (!saved || handoffRef.current) {
            return;
        }
        (async () => {
            try {
                const resumed = await postmortemService.getGame(saved);
                if (cancelled) {
                    return;
                }
                setState(resumed);
                // A review opened from the imported library (or from Play)
                // knows which seat was the person's; sit them there.
                if (resumed.player_color === 'white' || resumed.player_color === 'black') {
                    setOrientation(resumed.player_color);
                }
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
        const requestStarted = performance.now();
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
            learningService.event('correction_flow_error', {
                operation: 'pgn_import',
                duration_ms: Math.round(performance.now() - requestStarted),
                error_category: 'import_failed',
                completed: false,
            });
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
        let polls = 0;

        const poll = async () => {
            const next = await refreshReport(gameId);
            if (cancelled) {
                return;
            }
            // 'idle' on the first polls is not "no scan": the start request
            // is fired alongside the first read and can land after it. Keep
            // looking for a moment rather than leaving a report that never
            // arrives until the next navigation.
            const starting = next?.scan.status === 'idle' && next.scan.analysed < next.scan.total && polls < 4;
            if (next && (next.scan.status === 'running' || starting)) {
                timer = window.setTimeout(poll, scanPollDelay(polls++));
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
                window.setTimeout(() => void refreshReport(gameId), scanPollDelay(0));
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

    const openCorrection = useCallback(async (nodeId: string) => {
        await run(id => postmortemService.goto(id, nodeId));
        setPanel('correction');
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

    const boardFen = state?.fen ?? null;
    const board = useMemo(() => (boardFen ? new Chess(boardFen) : null), [boardFen]);

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

    // Check, checkmate, stalemate and draw read the same way as in Play and
    // Learn (../boardState.ts). The server's own `status.state` stays the
    // authority - it is passed in as the flags - and what is derived here is
    // the checked king's SQUARE, which is not on the wire and which the board
    // needs in order to say "this king" rather than only "check".
    const boardStatus = useMemo(() => readBoardStatus(state?.fen, {
        // A mated king IS in check - `status.state` is a single value and
        // reports the stronger of the two, so 'checkmate' has to imply
        // 'check' here or the mated king loses its red square, which is the
        // one position where it matters most.
        is_check: state?.status.state === 'check' || state?.status.state === 'checkmate',
        is_checkmate: state?.status.state === 'checkmate',
        is_stalemate: state?.status.state === 'stalemate',
        is_game_over: isOver,
    }), [state?.fen, state?.status.state, isOver]);

    // The end-state layer is for an ending YOU caused. On the mainline this
    // mode is a replay of a game that already finished, and stepping to the
    // last move of a mated game is a thing you do constantly - covering that
    // position would put a tint over the one move you came to look at. The
    // pm-alert strip already names the result there. On a branch the ending
    // is live and yours, and its reset is the branch's own way out.
    const endOverlay = state && !state.on_mainline ? boardStatus.end : null;

    /**
     * A click pair as a UCI string, with `piece` naming the promotion.
     *
     * Legality is decided server-side; an illegal move comes back as a 400
     * rather than being silently swallowed.
     *
     * This used to hardcode a queen, and CLAUDE.md §15 listed that as a
     * deliberate gap - stopping to ask which piece would interrupt the one
     * interaction the mode exists for. That reasoning is overturned rather
     * than forgotten. "What if I had underpromoted" is not an exotic
     * what-if: it is the knight that forks on arrival and the rook that
     * promotes without stalemating, and those are exactly the alternatives
     * somebody replays a lost endgame to check. A mode whose whole question
     * is "what would have happened if I had played this instead" could not
     * answer it for three of the four pieces.
     */
    const uciFor = useCallback((from: string, to: string, piece?: PromotionPiece): string | null => {
        if (!legalTargets.get(from)?.has(to)) {
            return null;
        }
        const canPromote = state?.legal_moves.includes(`${from}${to}q`);
        return `${from}${to}${canPromote ? (piece ?? 'q') : ''}`;
    }, [legalTargets, state]);

    // ----- Promotion ---------------------------------------------------------
    const [pendingPromotion, setPendingPromotion] = useState<PendingPromotion | null>(null);

    /** True when the picker has taken the move over; false to just play it. */
    const askPromotion = useCallback((from: string, to: string): boolean => {
        const fen = state?.fen;
        if (!fen || !isPromotionMove(fen, from, to)) {
            return false;
        }
        setPendingPromotion({ from, to, color: moverColor(fen, from) });
        return true;
    }, [state]);

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
        setThinking(true);
        setActivity('Checking your alternative against the engine…');
        const requestStarted = performance.now();
        try {
            const branched = await postmortemService.branch(gameId, uci);
            setState(branched);
            learningService.event('move_rendered', {
                game_id: gameId,
                node_id: branched.current_id,
                operation: 'alternative_move',
                duration_ms: Math.round(performance.now() - requestStarted),
                completed: true,
            });
            // The Correct tab watches this to notice when the move played in a
            // branch was the engine's own. It records nothing itself and
            // decides no legality - the branch above already did both.
            setLastBranchUci(uci);
            if (!branched.on_mainline && branched.status.state === 'playing') {
                setActivity('Coach is choosing a reply to your alternative…');
                try {
                    const replied = await postmortemService.aiMove(gameId);
                    setState(replied);
                    window.requestAnimationFrame(() => {
                        learningService.event('move_rendered', {
                            game_id: gameId,
                            node_id: replied.current_id,
                            operation: 'coach_branch_reply',
                            duration_ms: Math.round(performance.now() - requestStarted),
                            completed: true,
                        });
                    });
                } catch (exc) {
                    // The branch itself succeeded. Say the reply failed rather
                    // than rolling back a move the user just watched land.
                    setError(exc instanceof Error
                        ? `Your move is on the board, but the engine could not reply: ${exc.message}`
                        : 'Your move is on the board, but the engine could not reply.');
                    learningService.event('correction_flow_error', {
                        game_id: gameId,
                        operation: 'ai_move_generation',
                        duration_ms: Math.round(performance.now() - requestStarted),
                        error_category: 'branch_reply_failed',
                        completed: false,
                    });
                } finally {
                    setActivity(null);
                }
            }
        } catch (exc) {
            setError(exc instanceof Error ? exc.message : 'That move could not be played.');
        } finally {
            setBusy(false);
            setThinking(false);
            setActivity(null);
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
                setSelectedSquare(null);
                if (!askPromotion(selectedSquare, square)) {
                    void playAlternative(uci);
                }
                return;
            }
        }
        setSelectedSquare(legalTargets.has(square) ? square : null);
    }, [interactive, selectedSquare, uciFor, playAlternative, legalTargets, askPromotion]);

    const onPieceDrop = useCallback((from: Square, to: Square): boolean => {
        if (!interactive) {
            return false;
        }
        const uci = uciFor(from, to);
        if (!uci) {
            return false;
        }
        // Returns false so the pawn snaps home while the question is up: the
        // branch has not been created yet, so the board must not show it.
        if (askPromotion(from, to)) {
            return false;
        }
        void playAlternative(uci);
        return true;
    }, [interactive, uciFor, playAlternative, askPromotion]);

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
        // The checked king, over the last-move marks and under the move
        // hints. It is a fact about the position, so it is drawn whether or
        // not you are exploring - a replay that does not say which king is
        // in check is exactly as unreadable as a live game that does not.
        if (boardStatus.checkedKingSquare) {
            styles[boardStatus.checkedKingSquare] = { background: 'var(--sq-check-mark)' };
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
    }, [state, interactive, selectedSquare, legalTargets, occupied, boardStatus.checkedKingSquare]);

    // --- chat ----------------------------------------------------------------

    const sendMessage = useCallback(async () => {
        const message = draft.trim();
        if (!message || !gameId || pending) {
            return;
        }
        setDraft('');
        setPending(message);
        setChatError(null);
        const requestStarted = performance.now();
        try {
            const reply = await postmortemService.chat(gameId, message);
            setHistory(reply.history);
            requestAnimationFrame(() => {
                learningService.event('explanation_rendered', {
                    game_id: gameId,
                    operation: 'review_chat',
                    duration_ms: Math.round(performance.now() - requestStarted),
                    completed: true,
                });
            });
        } catch (exc) {
            setChatError(exc instanceof Error ? exc.message : 'The coach could not answer just now.');
            // The question goes back in the box rather than being lost, which
            // is what a failed send should cost: nothing.
            setDraft(message);
            learningService.event('correction_flow_error', {
                game_id: gameId,
                operation: 'review_chat',
                duration_ms: Math.round(performance.now() - requestStarted),
                error_category: 'coach_unavailable',
                completed: false,
            });
        } finally {
            setPending(null);
        }
    }, [draft, gameId, pending]);

    /** Empty the conversation on both sides - the Actions tab's "Clear chat". */
    const clearChat = useCallback(async () => {
        if (!gameId) {
            return;
        }
        setChatError(null);
        try {
            await postmortemService.clearChat(gameId);
            setHistory([]);
        } catch (exc) {
            setChatError(exc instanceof Error ? exc.message : 'Could not clear the conversation.');
        }
    }, [gameId]);

    // --- render ---------------------------------------------------------------

    if (!state && (opening || openingError)) {
        return (
            <div className="pm">
              <div className="pm-empty">
                <div className={`pm-handoff ${openingError ? 'is-error' : 'is-busy'}`} role="status" aria-live="polite">
                    {openingError ? (
                        <>
                            <p className="pm-handoff-title">The review could not be opened</p>
                            <p className="pm-handoff-body">{openingError}</p>
                            <div className="pm-handoff-actions">
                                {onBackToPlay && (
                                    <button type="button" className="action-btn" onClick={onBackToPlay}>
                                        Back to Play
                                    </button>
                                )}
                                <button
                                    type="button"
                                    className="action-btn"
                                    onClick={() => setOpeningError(null)}
                                >
                                    Paste a PGN instead
                                </button>
                            </div>
                        </>
                    ) : (
                        <>
                            <span className="thinking-dots mini" aria-hidden="true"><span></span><span></span><span></span></span>
                            <p className="pm-handoff-title">Analysing the game you just played…</p>
                            <p className="pm-handoff-body">Finding key decisions and preparing your review.</p>
                        </>
                    )}
                </div>
              </div>
            </div>
        );
    }

    if (!state) {
        return (
            <div className="pm">
                <PostMortemDropzone
                    onPgn={openGame}
                    busy={importing}
                    error={importError}
                    onDismissError={() => setImportError(null)}
                />
                {/* The way into the multi-game workflow.
                    At the BOTTOM of Review and not in the header, because this is the
                    second thing somebody wants, not the first: they came here to look at
                    one game, and the idea of looking at twenty only becomes interesting
                    once they have. Review itself is untouched - upload a PGN, get an
                    immediate analysis - and this complements it rather than replacing it. */}
                <Link className="pm-profile-cta" to="/profile">
                    <span className="pm-profile-cta-title">Build improvement profile</span>
                    <span className="pm-profile-cta-body">
                        Import many games and find the mistakes you keep making, rather than
                        the ones you made once.
                    </span>
                </Link>
            </div>
        );
    }

    const white = state.headers.White ?? 'White';
    const black = state.headers.Black ?? 'Black';

    /**
     * One side's seat: the name, which colour they had, and how the game
     * ended for them.
     *
     * Everything here is read from the PGN's own headers or from the result
     * string the server parsed out of them - nothing is inferred, and a header
     * the file did not carry falls back to the plain word ("White", "Black")
     * rather than to a guess. `pm-seat-result` is the piece a tester could not
     * find anywhere: "1-0" tells you who won only if you already know who was
     * White, which is exactly what they did not know.
     */
    function renderSeat(side: 'white' | 'black', where: 'top' | 'bottom') {
        const name = side === 'white' ? white : black;
        const elo = state!.headers[side === 'white' ? 'WhiteElo' : 'BlackElo'];
        const result = state!.result;
        // "1-0" means White won, "0-1" Black, "1/2-1/2" a draw, "*" unfinished.
        const outcome = result === '1-0'
            ? (side === 'white' ? 'won' : 'lost')
            : result === '0-1'
                ? (side === 'black' ? 'won' : 'lost')
                : result === '1/2-1/2' ? 'drew' : null;
        return (
            <div className={`pm-seat is-${where}`}>
                <span className={`pm-seat-disc ${side}`} aria-hidden="true" />
                <span className="pm-seat-identity">
                    <span className="pm-seat-name">{name}</span>
                    <span className="pm-seat-sub">
                        {side === 'white' ? 'White' : 'Black'}
                        {elo ? ` · ${elo}` : ''}
                    </span>
                </span>
                {outcome && (
                    <span className={`pm-seat-result is-${outcome}`}>
                        {outcome === 'drew' ? 'draw' : outcome}
                    </span>
                )}
            </div>
        );
    }
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
                            // python-chess fills absent tags with "?" placeholders;
                            // those are not values and are not shown.
                            /\?/.test(state.headers.Event ?? '') ? null : state.headers.Event,
                            /\?/.test(state.headers.Date ?? '') ? null : state.headers.Date,
                            `${Math.ceil(state.total_plies / 2)} moves`,
                            state.game_count > 1 ? `first of ${state.game_count} games in ${state.source_name}` : state.source_name,
                        ].filter(Boolean).join(' · ')}
                    </span>
                </div>

                <div className="pm-board-column" ref={boardColumnRef}>
                    {/* Who is who, and which way round you are looking.
                        ------------------------------------------------
                        A tester could not tell which player was White and
                        which was Black. The names were only in the heading
                        ("Hikaru vs Magnus"), which states the pairing but not
                        the sides, and nothing on screen said whose view the
                        board was drawn from.

                        So the two names sit where a chess clock puts them:
                        above and below the board, the side you are looking
                        from at the bottom. They follow the rotation, because a
                        label that stayed put while the board turned would be
                        worse than no label at all. One function renders both
                        seats, so the two cannot disagree about who is where. */}
                    {renderSeat(orientation === 'white' ? 'black' : 'white', 'top')}
                    {/* The frame says which of the two states you are in. A
                        branch is a different colour of border and a label, not
                        a modal or a mode switch - you can still see the game's
                        move list beside it. */}
                    <div className={`pm-board-wrapper ${state.on_mainline ? '' : 'is-branch'} ${thinking ? 'is-thinking' : ''}`}>
                        <Chessboard
                            position={state.fen}
                            boardWidth={boardSize}
                            boardOrientation={orientation}
                            arePiecesDraggable={interactive}
                            isDraggablePiece={({ sourceSquare }) => legalTargets.has(sourceSquare)}
                            onPieceDrop={onPieceDrop}
                            // Auto-queen, matching what click-to-move
                            // has always done here: without this
                            // react-chessboard opens its own promotion
                            // dialog on a drag to the last rank, so the
                            // same move would ask a question one way and
                            // not the other.
                            autoPromoteToQueen
                            onSquareClick={onSquareClick}
                            customSquareStyles={squareStyles}
                            animationDuration={150}
                            customPieces={customPieces}
                            customNotationStyle={BOARD_NOTATION_STYLE}
                            customDarkSquareStyle={{ backgroundColor: 'var(--board-dark)' }}
                            customLightSquareStyle={{ backgroundColor: 'var(--board-light)' }}
                        />
                        {/* Branch endings only - see endOverlay above. The
                            reset is this mode's own way off a branch, under
                            the label it already carries in the transport. */}
                        <BoardEndState
                            end={endOverlay}
                            onReset={() => void run(id => postmortemService.returnToGame(id))}
                            resetLabel="Back to the game"
                        />
                        {/* Underpromotion in a branch, which this mode used to
                            refuse - see uciFor. The picker positions itself
                            from the square AND the orientation, so it stays on
                            the board when the review is rotated. */}
                        <PromotionPicker
                            pending={pendingPromotion}
                            boardSize={boardSize}
                            orientation={orientation}
                            pieceTheme={pieceTheme}
                            onSelect={piece => {
                                const move = pendingPromotion;
                                setPendingPromotion(null);
                                if (!move) return;
                                const uci = uciFor(move.from, move.to, piece);
                                if (uci) void playAlternative(uci);
                            }}
                            onCancel={() => setPendingPromotion(null)}
                        />
                    </div>

                    {/* The near seat is whichever side the board is drawn
                        from - that is what "you are looking from Black's
                        side" means. The far seat above the board is the
                        other one. */}
                    {renderSeat(orientation, 'bottom')}

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
                            /* Two different ends, because `/forward` is two
                               different walks (postmortem_api.step_forward):
                               on the mainline it follows the GAME, so the end
                               is its last ply; inside a branch it follows the
                               tree, so the end is a node with no children. The
                               mainline half was guarded and the branch half
                               was not, which left Next enabled at the tip of
                               every what-if - the one place in this mode where
                               there is provably nothing ahead of you. */
                            disabled={busy || (state.on_mainline
                                ? state.ply >= state.total_plies
                                : state.node.children.length === 0)}
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
                            {exploring ? 'Playing' : (<>
                                <span className="btn-label-full">Play a different move</span>
                                <span className="btn-label-tight">Try a move</span>
                            </>)}
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
                                <span className="btn-label-full">Back to the game</span>
                                <span className="btn-label-tight">Back</span>
                            </button>
                        )}
                    </div>

                    {/* The status line and the meta row share one line.
                        ---------------------------------------------------
                        Everywhere else in the app these are two rows, and
                        here they are one because Review could not afford the
                        second. Two player seats were added above and below
                        the board this sprint (a tester could not tell which
                        player was which), and at 1280x800 that put the page
                        32px past the viewport with the board already on its
                        240px floor - so the fitter had nothing left to give.
                        Measured, not guessed: hiding the seats at runtime took
                        the overflow to zero and hiding this row as well took
                        the column another 42px below that.

                        Merging them is the right 42px to reclaim because the
                        status line is blank almost all of the time - it says
                        one sentence while the engine answers a what-if - so a
                        row of its own was reserving height for emptiness. It
                        is still ALWAYS in the layout, which is the rule that
                        matters (§11): the sentence appearing does not move the
                        controls beside it. */}
                    <div className="ws-meta pm-footer-row">
                        <span
                            className={`pm-status ${thinking ? 'is-busy' : ''}`}
                            role="status"
                            aria-live="polite"
                        >
                            {activity ?? (thinking ? 'Coach is reviewing the position…' : '')}
                        </span>
                        <button
                            type="button"
                            className="pm-rotate-btn"
                            onClick={() => setOrientation(o => (o === 'white' ? 'black' : 'white'))}
                            /* Named for the result, not the action. "Rotate"
                               tells you what the button does to the board;
                               "View as Black" tells you what you will be
                               looking at, which is the thing you actually
                               want - and it doubles as a statement of which
                               side you are NOT currently on. */
                            title={orientation === 'white'
                                ? `Turn the board round and follow the game from ${black}'s side`
                                : `Turn the board round and follow the game from ${white}'s side`}
                        >
                            {orientation === 'white' ? 'View as Black' : 'View as White'}
                        </button>
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
                                    // Wired to the panel they switch, as Play's
                                    // and Learn's are. Without it a screen
                                    // reader is told these are tabs and then
                                    // given nothing to say what they control.
                                    id={`pm-tab-${item.id}`}
                                    aria-controls="pm-panel"
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

                    <div
                        className="pm-canvas-inner"
                        id="pm-panel"
                        role="tabpanel"
                        aria-labelledby={`pm-tab-${panel}`}
                        tabIndex={0}
                    >
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

                        {panel === 'correction' && (
                            <CorrectionPanel
                                gameId={gameId}
                                nodeId={state.current_id}
                                moveLabel={
                                    state.ply === 0 || !analysis
                                        ? null
                                        : `${Math.ceil(state.ply / 2)}${
                                              analysis.color === 'white' ? '.' : '...'
                                          } ${analysis.san}`
                                }
                                // Nothing to diagnose on the starting position,
                                // and nothing to diagnose without the engine's
                                // packet for the move - the scan may not have
                                // reached it yet, in which case the panel says
                                // to pick a move rather than inventing one.
                                canDiagnose={state.ply > 0}
                                opponentMove={Boolean(state.player_color && analysis && analysis.color !== state.player_color)}
                                onMainline={state.on_mainline}
                                pieceTheme={pieceTheme}
                                // Turning on Review's own explore mode. The
                                // move itself is then played on the real board
                                // through the branch flow that already exists.
                                onRequestExplore={async () => {
                                    const beforeDecision = state.node.parent_id;
                                    if (!beforeDecision) {
                                        throw new Error('The position before this decision is unavailable.');
                                    }
                                    setSelectedSquare(null);
                                    await run(id => postmortemService.goto(id, beforeDecision));
                                    setExploring(true);
                                }}
                                lastBranchUci={lastBranchUci}
                            />
                        )}

                        {panel === 'actions' && (
                            <div className="actions-list">
                                <div className="actions-item">
                                    <BoardSizeControl value={boardSizePref} onChange={setBoardSizePref} />
                                    <span className="actions-note">How large the board is drawn. Auto follows the window.</span>
                                </div>
                                {/* The way into the multi-game workflow, while a
                                    game is open. It sat under the whole layout,
                                    and that 100px of page below the board was
                                    exactly what the fitter took off the board to
                                    keep the page from scrolling - Review's board
                                    was a size smaller than Play's and Learn's at
                                    every setting. The empty canvas still carries
                                    the full card, where it is the second thing a
                                    visitor wants. */}
                                <div className="actions-item">
                                    <Link className="action-btn actions-profile-link" to="/profile">
                                        Improvement profile
                                    </Link>
                                    <span className="actions-note">
                                        Import many games and find the mistakes you keep making, rather than
                                        the ones you made once.
                                    </span>
                                </div>
                                <CoachStyleSettings />
                                <div className="actions-item">
                                    <button
                                        type="button"
                                        className="action-btn actions-clear-chat"
                                        onClick={() => void clearChat()}
                                        disabled={pending !== null || history.length === 0}
                                    >
                                        Clear chat
                                    </button>
                                    <span className="actions-note">
                                        Empties the conversation. The coach forgets it too; the review stays open.
                                    </span>
                                </div>
                            </div>
                        )}
                        {panel === 'report' && (
                            <PostMortemReport
                                scan={report?.scan ?? state.scan}
                                summary={report?.summary ?? state.summary}
                                curve={report?.curve ?? []}
                                moves={report?.moves ?? state.moves}
                                currentPly={state.ply}
                                onSelect={nodeId => void openCorrection(nodeId)}
                                onRetry={() => void startScan()}
                                playerColor={state.player_color ?? null}
                            />
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
