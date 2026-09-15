import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import type { Square } from 'chess.js';
import { getCustomPieces, PIECE_THEME_LIST } from '../pieceThemes';
import { EmptyState } from './EmptyState';
import { EvalBar } from './EvalBar';
import { BoardEndState } from './BoardEndState';
import { readBoardStatus } from '../boardState';
import { useBoardSizing } from '../hooks/useBoardScale';
import { BoardSizeControl } from './BoardSizeControl';
import { CoachStyleSettings } from './CoachStyleSettings';
import { PromotionPicker } from './PromotionPicker';
import { isPromotionMove, moverColor } from './promotion';
import type { PendingPromotion, PromotionPiece } from './promotion';
import { DEFAULT_PROFILE_ID, OPPONENT_PROFILES, profileLabel } from '../opponentProfiles';
import { renderFormattedText } from '../formatText';
import type { PieceThemeName } from '../pieceThemes';
import { sandboxService } from '../services/sandboxService';
import { profileService } from '../services/profileService';
import type {
    SandboxNode,
    SandboxState,
    NarrationStatus,
    SandboxChatTurn,
    SandboxTranscriptEntry,
} from '../types/sandbox';
import './Sandbox.css';

// Sandbox Learner Mode - the AI plays both sides to demonstrate a line,
// narrating as a coach rather than as the player.
//
// This is a sibling of ChessBoard, not a section inside it. ChessBoard is
// already 1832 lines and owns the real game; the sandbox owns a session id
// and nothing else, which is precisely the isolation the backend was built
// around (a SandboxSession holds no learning-service handle, so it *cannot*
// write to the player's history).
//
// Two voices are deliberately kept apart on screen:
//   - `explanation` is the AI player justifying its own choice.
//   - `narration` is the coach telling a student what the move accomplishes.
// Collapsing them into one block would throw away the thing the sandbox
// exists to show. Ice (--cp-primary) is the AI's voice, and it is spent
// only on narration - not on square highlights, not on the tree, not on
// engine scores. Those borrow the real game's own colours instead.
//
// The right-hand column is three panels behind one tab row rather than
// three columns, because the board plus a 380px panel already fills a
// laptop screen and the three are read one at a time:
//   Coach       - the running commentary, oldest first
//   Line        - the whole move tree, branches included, every node clickable
//   Why not?    - Stockfish's ranking here, against what has been tried

/**
 * Rank and file labels, shared with the real game - see BOARD_NOTATION_STYLE in
 * ChessBoard.tsx for why the library's own default is not good enough here.
 */
/**
 * Whether a composer message is a position being handed over rather than a
 * request written in words.
 *
 * This mirrors `scenario_service.looks_like_fen` / `looks_like_pgn`, and the
 * duplication is deliberate and bounded: it decides ROUTING only - whether to
 * ask the intent classifier or go straight to a build - and the server re-runs
 * its own parser on whatever arrives. A disagreement between the two costs one
 * round trip. It cannot produce a wrong board, which is the property that
 * makes a second copy acceptable here.
 */
const FEN_RE = /^\s*(?:[rnbqkpRNBQKP1-8]{1,8}\/){7}[rnbqkpRNBQKP1-8]{1,8}(?:\s+[wb](?:\s+\S+){0,4})?\s*$/;
const PGN_TAG_RE = /^\s*\[\s*\w+\s+"/m;
const PGN_MOVE_RE = /\b\d+\s*\.\s*(?:[NBRQKO][a-h1-8xO\-+#=]|[a-h][1-8x])/g;

function looksPasted(text: string): boolean {
    const trimmed = (text ?? '').trim();
    if (!trimmed) return false;
    if (FEN_RE.test(trimmed)) return true;
    if (PGN_TAG_RE.test(trimmed)) return true;
    // Two numbered moves, not one: "what happens after 1. e4" is a question
    // for the coach, and setting the board to move one would be the wrong half
    // of the app answering it.
    return (trimmed.match(PGN_MOVE_RE) ?? []).length >= 2;
}

const BOARD_NOTATION_STYLE: Record<string, string | number> = {
    color: 'var(--board-notation)',
    fontFamily: 'var(--font-mono)',
    fontWeight: 700,
    textShadow:
        '0 0 2px var(--board-notation-halo), 0 0 2px var(--board-notation-halo),'
        + ' 0 0 3px var(--board-notation-halo), 0 0 4px var(--board-notation-halo)',
};

/** How often to re-poll a node whose narration is still in flight. */
const NARRATION_POLL_MS = 1500;

/** Breathing room between auto-played half-moves so a line can be followed. */
const AUTOPLAY_GAP_MS = 650;


/** Which of the right-hand panels is showing. */
type Panel = 'tree' | 'chat' | 'board' | 'actions';

/**
 * Learner Mode's own piece set.
 *
 * Separate from the real game's `chess-piece-theme` on purpose: the two modes
 * are looked at for different reasons - one is a game you are playing, the
 * other is a demonstration you are reading - and a set that suits one is not
 * automatically the set that suits the other.
 *
 * It DEFAULTS to whatever the game is using, though, so the first trip into
 * Learner Mode does not silently change the pieces under you. Once it is set
 * here it stays set here, and the two stop tracking each other.
 */
const SANDBOX_THEME_KEY = 'sandbox-piece-theme';
const GAME_THEME_KEY = 'chess-piece-theme';

/** Which panel was open. Same reasoning as the real game's rail section. */
const SANDBOX_PANEL_KEY = 'sandbox-panel';

/** Whether the eval bar is showing. Off by default - see the state below. */
const SANDBOX_EVAL_KEY = 'sandbox-eval-bar';

/**
 * An evaluation as a share of the bar, 0 (Black winning) to 1 (White winning).
 *
 * Centipawns are squashed into +/-1000 - ten pawns - because past that one
 * side is winning so decisively that the exact number stops meaning anything
 * to look at, and without the clamp a single blunder pins the bar to one end
 * and it never moves again. The same compression the real game's bar uses, so
 * the two read the same way.
 */
function evalShare(evaluation: { score: number | null; mate_in: number | null }): number {
    if (evaluation.mate_in !== null) {
        return evaluation.mate_in > 0 ? 1 : 0;
    }
    const cp = Math.max(-1000, Math.min(1000, evaluation.score ?? 0));
    return 0.5 + (cp / 1000) * 0.5;
}

/** "+2.1", "-4.6", "M3", "-M1" - the notation a chess reader already knows. */
function evalLabel(evaluation: { score: number | null; mate_in: number | null }): string {
    if (evaluation.mate_in !== null) {
        return evaluation.mate_in > 0 ? `M${evaluation.mate_in}` : `-M${Math.abs(evaluation.mate_in)}`;
    }
    if (evaluation.score === null) {
        return '0.0';
    }
    const pawns = evaluation.score / 100;
    return `${pawns > 0 ? '+' : ''}${pawns.toFixed(1)}`;
}

/**
 * The session id, so a reload comes back to the same board.
 *
 * Sessions live in memory on the server with an hour's idle sweep and a hard
 * cap of 50, so this is a hint and never a promise: the id is re-fetched on
 * mount and a 404 simply means opening a fresh one, which is exactly what used
 * to happen every time. When it does still exist, the position, the move tree
 * and the coach's transcript all come back with it - which is the difference
 * between a refresh costing you a demonstration and costing you nothing.
 */
const SANDBOX_SESSION_KEY = 'sandbox-session';

/**
 * The side to move in the position a session STARTED from.
 *
 * The board is oriented to whoever is being taught, and that is decided once
 * when the position is opened. A node's FEN has the side to move as its second
 * field, and the root node is the starting position.
 */
function rootTurn(state: SandboxState): 'white' | 'black' {
    const root = state.tree.nodes[state.tree.root_id];
    return root?.fen.split(' ')[1] === 'b' ? 'black' : 'white';
}

function readStored(key: string): string | null {
    try {
        return localStorage.getItem(key);
    } catch {
        return null;
    }
}

function writeStored(key: string, value: string | null): void {
    try {
        if (value === null) {
            localStorage.removeItem(key);
        } else {
            localStorage.setItem(key, value);
        }
    } catch {
        // localStorage unavailable - nothing here has to persist to work.
    }
}

function initialSandboxTheme(): PieceThemeName {
    const valid = (v: string | null): v is PieceThemeName =>
        !!v && PIECE_THEME_LIST.some(theme => theme.id === v);
    try {
        const own = localStorage.getItem(SANDBOX_THEME_KEY);
        if (valid(own)) {
            return own;
        }
        // Never chosen here: inherit the game's, so the board looks the same
        // on the way in.
        const game = localStorage.getItem(GAME_THEME_KEY);
        if (valid(game)) {
            return game;
        }
    } catch {
        // localStorage unavailable (private browsing) - fall through.
    }
    return 'stencil';
}

// Chat leads now: it is the only place a position gets built, so it is where
// someone opening Learner Mode for the first time has to land.
//
// There is no Coach tab any more. It listed the current line with the coach's
// commentary under each move, on a different tab from the box you would ask
// about that commentary in - and it was rewritten every time the line was
// stepped back. The commentary is a message in the conversation now (a
// `coach` transcript entry), where it stays put and can be asked about.
const PANELS: { id: Panel; label: string; sub: string }[] = [
    { id: 'chat', label: 'Chat', sub: 'The coach on every move, and anything you ask about the position' },
    { id: 'tree', label: 'Line', sub: 'Every position explored - click any move to go there' },
    { id: 'board', label: 'Board', sub: 'The piece set used here, kept separate from the game' },
    { id: 'actions', label: 'Actions', sub: 'Things you reach for now and then' },
];

interface NarrationEntry {
    status: NarrationStatus;
    narration: string | null;
}

/**
 * The move number to print in front of a move, read off the *parent's* FEN.
 *
 * A node's own FEN is the position *after* its move, and Black's move
 * increments the full-move counter - so numbering from `node.fen` prints
 * every black move one too high. The parent's FEN is the position the move
 * was played from, which is exactly the number a scoresheet would show.
 */
function moveNumber(node: SandboxNode, parent: SandboxNode | undefined): string {
    const fullmove = parent ? parent.fen.split(' ')[5] : '1';
    return node.mover === 'white' ? `${fullmove}.` : `${fullmove}...`;
}

export function Sandbox() {
    // Two steps: how wide the board would LIKE to be, then how tall it is
    // allowed to be once everything under it has been measured.
    //
    // The second half exists because this column's chrome is not a fixed
    // height. The control row wraps to a different number of lines at
    // different widths, so the single constant useBoardSize takes was correct
    // at exactly one viewport - measured at 1440 it overflowed 1280 by 92px,
    // and sized for 1280 it wasted 49px of board at 1440. Adding the alert
    // strip and the eval bar made that worse in both directions at once.
    // useFittedBoardSize measures what is actually there instead, which also
    // means the next row added under the board does not need anyone to
    // remember to retune a number.
    const boardColumnRef = useRef<HTMLDivElement | null>(null);
    // Preference, ceiling, fit and shrink in one call - the growing and
    // shrinking halves are not symmetrical and the reasoning lives in one
    // place rather than three. See hooks/useBoardScale.
    const { pref: boardSizePref, setPref: setBoardSizePref, boardSize } =
        useBoardSizing(boardColumnRef, 0);
    // Off while the layout is stacked: there the panel sits below the board and
    // IT is what overflows, so correcting the board removes nothing and the
    // fitter runs it down to its floor. See hooks/useStacked.ts.

    const [state, setState] = useState<SandboxState | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<boolean>(false);
    const [booting, setBooting] = useState<boolean>(true);

    // Distinct from `busy`: this is specifically "a Gemini move call is out",
    // which is the one wait long enough (Gemini's long tail runs to 14s) that
    // four dead buttons need an explanation next to them. The ref shadows it
    // for the same reason autoPlayRef does - Pause has to know whether a move
    // is in flight from inside a callback that never re-renders.
    const [thinking, setThinking] = useState<boolean>(false);
    const thinkingRef = useRef<boolean>(false);

    // Pause pressed while a half-move was still out. The controls stay
    // disabled until it lands, so this is what turns that wait from four
    // dead buttons into a sentence.
    const [pausing, setPausing] = useState<boolean>(false);

    const [generating, setGenerating] = useState<boolean>(false);

    // Held separately from `state` on purpose. Only POST /scenario returns a
    // `scenario` block; every other endpoint returns the same body without
    // it. Reading it off `state` meant the description - and more importantly
    // the honest notes ("closest I could get", "verified winning for white") -
    // vanished the moment the first move was played.
    //
    // Narrowed from the whole SandboxScenario response to the two fields
    // anything actually reads. Only POST /scenario returns that response, so
    // holding it meant a session RESUMED after a reload - which comes back
    // through a plain GET carrying only the description - had no way to say
    // what it was for without inventing the fields it does not have.
    const [brief, setBrief] = useState<
        { description: string; notes: string | null; favorMet: boolean | null } | null
    >(null);

    const [autoPlay, setAutoPlay] = useState<boolean>(false);
    // Read inside the auto-play loop, which outlives the render that started
    // it - a state value captured in the closure would be stale forever.
    const autoPlayRef = useRef<boolean>(false);
    // Whether a loop is still RUNNING, as opposed to whether it should keep
    // going. The two differ for as long as it takes an in-flight half-move to
    // land, and that gap is where a second loop used to be started.
    const autoPlayLoopRef = useRef<boolean>(false);

    // Narration arrives after the move it describes, so it lives beside the
    // tree rather than inside our copy of it. Keyed by node id - never by
    // ply index. Ids are minted once and never reused, so a late arrival
    // either finds its node or finds nothing; indices get recycled by a
    // rewind and would let one ply's narration land on another's.
    const [narrations, setNarrations] = useState<Record<string, NarrationEntry>>({});

    // Fixed when a position is opened, and never from the *current* turn -
    // deriving it from whose move it is flips the board on every half-move,
    // which makes a demonstration impossible to follow. The side to move in
    // the starting position is the side being taught, so orient to them.
    const [orientation, setOrientation] = useState<'white' | 'black'>('white');

    const [panel, setPanel] = useState<Panel>(() => {
        const stored = readStored(SANDBOX_PANEL_KEY);
        return PANELS.some(p => p.id === stored) ? (stored as Panel) : 'chat';
    });
    useEffect(() => { writeStored(SANDBOX_PANEL_KEY, panel); }, [panel]);

    // --- coach chat -------------------------------------------------------
    // Its own transcript, its own endpoint, its own model chain. Nothing here
    // touches the real game's chat: the two conversations are about different
    // positions and must never appear in each other's history.
    //
    // The transcript is shown in two pieces, and the split is what lets the
    // conversation survive a rebuild.
    //
    //   `frozen`   entries the server is no longer the owner of - everything
    //              from before the current position, plus the local-only
    //              dividers and confirmations the server has no concept of.
    //   `live`     the server's own transcript, which it returns in full on
    //              every reply and which is what gets replayed to Gemini.
    //   `absorbed` how many of `live`'s turns have already been copied into
    //              `frozen`. Rendering is frozen ++ live.slice(absorbed), so
    //              a server reply can replace `live` wholesale - as it must,
    //              since the server owns that history - without ever deleting
    //              a divider or duplicating a turn.
    //
    // Building a position resets `live` to empty (a scenario opens a fresh
    // session), which is exactly when everything before it becomes frozen.
    //
    // Held as ONE piece of state rather than three, and that is load-bearing.
    // As three, `freezeTranscript` read `live` and `absorbed` out of the
    // render closure - so calling it twice inside one handler (which the
    // build path does: once to close off the old conversation, once to add
    // the divider) saw the same pre-update values both times and copied the
    // same turns into `frozen` twice. It showed up as every question appearing
    // a second time above the rule. A functional update on a single object
    // always sees the current value, and cannot desynchronise the count from
    // the list it is counting.
    const [chat, setChat] = useState<{
        frozen: SandboxTranscriptEntry[];
        live: SandboxChatTurn[];
        absorbed: number;
    }>({ frozen: [], live: [], absorbed: 0 });
    const [chatInput, setChatInput] = useState<string>('');
    const [chatSending, setChatSending] = useState<boolean>(false);
    const [chatError, setChatError] = useState<string | null>(null);
    const chatLogRef = useRef<HTMLDivElement | null>(null);

    // A build request made while there are moves on the board. Held rather
    // than acted on, because building replaces the line and there is no undo.
    const [pendingBuild, setPendingBuild] = useState<string | null>(null);

    const transcript: SandboxTranscriptEntry[] = useMemo(() => [
        ...chat.frozen,
        ...chat.live.slice(chat.absorbed).map(t => ({ kind: 'turn' as const, role: t.role, text: t.text })),
    ], [chat]);

    // Which nodes already have their coach entry in the transcript, keyed on
    // the session too because a rebuilt session has fresh ids. A ref, not
    // state: it is bookkeeping for the effect below and must never cause a
    // render of its own.
    const coachedRef = useRef<Set<string>>(new Set());

    /** Move everything currently on screen into `frozen`, then append `tail`. */
    const freezeTranscript = useCallback((tail: SandboxTranscriptEntry[]) => {
        setChat(prev => ({
            frozen: [
                ...prev.frozen,
                ...prev.live.slice(prev.absorbed).map(t => ({ kind: 'turn' as const, role: t.role, text: t.text })),
                ...tail,
            ],
            live: prev.live,
            absorbed: prev.live.length,
        }));
    }, []);

    /** A new session: the server's transcript starts empty, ours carries on. */
    const startFreshHistory = useCallback(() => {
        setChat(prev => ({ ...prev, live: [], absorbed: 0 }));
    }, []);

    // The board is deliberately inert during a demonstration: the user is
    // watching, and a stray click that silently branched the line would be a
    // confusing way to find out the tree branches at all. Taking over is an
    // explicit act (decision 2 - it branches permanently).
    const [takeover, setTakeover] = useState<boolean>(false);
    const [narrateMine, setNarrateMine] = useState<boolean>(false);
    const [selectedSquare, setSelectedSquare] = useState<Square | null>(null);

    // null means "whatever the session says". Held as a draft so changing the
    // dropdown doesn't wipe a line the user is halfway through reading - the
    // restart is a separate, labelled click.
    const [profileDraft, setProfileDraft] = useState<string | null>(null);

    //
    // Off by default, and the request only goes out while it is on. The
    // endpoint is a full-depth search sharing one engine lock with move
    // selection, so a bar nobody asked for would slow the demonstration it is
    // supposed to be describing. Same reasoning as the real game's "Engine
    // numbers" switch, and persisted for the same reason.
    const [showEval, setShowEval] = useState<boolean>(() => readStored(SANDBOX_EVAL_KEY) === 'true');
    useEffect(() => { writeStored(SANDBOX_EVAL_KEY, String(showEval)); }, [showEval]);
    const [evaluation, setEvaluation] = useState<{ score: number | null; mate_in: number | null } | null>(null);

    const [pieceTheme, setPieceTheme] = useState<PieceThemeName>(initialSandboxTheme);
    useEffect(() => {
        try {
            localStorage.setItem(SANDBOX_THEME_KEY, pieceTheme);
        } catch {
            // localStorage unavailable - the choice just won't outlive the tab.
        }
    }, [pieceTheme]);
    const customPieces = useMemo(() => getCustomPieces(pieceTheme), [pieceTheme]);

    const sessionId = state?.session_id ?? null;

    /** Empty the conversation on both sides - the Actions tab's "Clear chat". */
    const clearChat = useCallback(async () => {
        if (!sessionId) {
            return;
        }
        setChatError(null);
        try {
            await sandboxService.clearChat(sessionId);
            setChat({ frozen: [], live: [], absorbed: 0 });
            setPendingBuild(null);
        } catch (err) {
            setChatError(err instanceof Error ? err.message : 'Could not clear the conversation.');
        }
    }, [sessionId]);


    // ----- session lifecycle -------------------------------------------------

    /** Fold a fresh server payload in, including any narration it already carries. */
    const absorb = useCallback((next: SandboxState) => {
        setState(next);
        setNarrations(prev => {
            const merged = { ...prev };
            for (const node of Object.values(next.tree.nodes)) {
                // The tree is authoritative for anything it knows about; a
                // poll only ever fills in what was pending when it arrived.
                if (node.narration_status !== 'none' || !merged[node.id]) {
                    merged[node.id] = {
                        status: node.narration_status,
                        narration: node.narration,
                    };
                }
            }
            return merged;
        });
    }, []);

    const clearSelection = useCallback(() => setSelectedSquare(null), []);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            // Resume first. Sessions are in-memory server-side with an hour's
            // idle sweep and die with the process, so this is a hint and never
            // a promise - but when it holds, the position, the whole move tree
            // and the coach's transcript come back with it, and a reload stops
            // costing a demonstration. A miss is a 404 and simply means
            // opening a fresh one, which is what always used to happen.
            const stored = readStored(SANDBOX_SESSION_KEY);
            if (stored) {
                try {
                    const resumed = await sandboxService.getSession(stored);
                    if (cancelled) {
                        return;
                    }
                    // From the ROOT's side to move, not the current one.
                    // Orientation is fixed when a position is opened, from the
                    // side being taught - deriving it from whose turn it
                    // happens to be flips the board on every half-move, which
                    // is the bug the original code was written to avoid. On a
                    // fresh session the two are the same value, so resuming
                    // was the one path where reading `turn` was wrong: reload
                    // a line that had reached mate and the board came back
                    // upside down, because the mated side was to move.
                    setOrientation(rootTurn(resumed));
                    if (resumed.scenario_description) {
                        setBrief({ description: resumed.scenario_description, notes: null, favorMet: null });
                    }
                    if (resumed.practice) {
                        // A practice position is the student's to play from
                        // the first move; no "Take over" step first.
                        setTakeover(true);
                    }
                    absorb(resumed);
                    try {
                        const past = await sandboxService.chatHistory(stored);
                        if (!cancelled) {
                            setChat({ frozen: [], live: past.history, absorbed: 0 });
                        }
                    } catch {
                        // A board with no transcript is still the board.
                    }
                    if (!cancelled) {
                        setBooting(false);
                    }
                    return;
                } catch {
                    // Swept, expired, or the server restarted. Fall through.
                    writeStored(SANDBOX_SESSION_KEY, null);
                }
            }
            try {
                const next = await sandboxService.createSession(DEFAULT_PROFILE_ID);
                if (cancelled) {
                    // StrictMode mounts, unmounts and remounts this effect in
                    // development, so the first session is already orphaned by
                    // the time it arrives. Drop it here rather than leaving it
                    // to occupy one of the store's 50 slots for an hour.
                    void sandboxService.deleteSession(next.session_id).catch(() => {});
                    return;
                }
                setOrientation(next.turn);
                absorb(next);
            } catch (err) {
                if (!cancelled) {
                    setError(err instanceof Error ? err.message : 'Could not open a sandbox session.');
                }
            } finally {
                if (!cancelled) {
                    setBooting(false);
                }
            }
        })();
        return () => { cancelled = true; };
    }, [absorb]);

    // Sessions are in-memory server-side with a 1h idle sweep and a hard cap
    // of 50. Dropping ours on the way out keeps a browsing user from filling
    // that cap with abandoned boards.
    useEffect(() => {
        if (!sessionId) {
            return;
        }
        writeStored(SANDBOX_SESSION_KEY, sessionId);
        return () => {
            autoPlayRef.current = false;
            // Only reached on a real unmount or an id change - a page reload
            // tears the tab down without running React cleanups, which is
            // precisely why the session is still there to be resumed.
            writeStored(SANDBOX_SESSION_KEY, null);
            void sandboxService.deleteSession(sessionId).catch(() => {
                // Best effort - the TTL sweep collects it either way.
            });
        };
    }, [sessionId]);

    // ----- narration polling -------------------------------------------------

    // Poll only the nodes actually waiting on Gemini, and stop the moment
    // none are. Polling the whole tree to discover one string would grow
    // with the length of the demonstration, which is why the backend
    // exposes a per-node endpoint at all.
    const pendingIds = useMemo(() => {
        if (!state) {
            return [] as string[];
        }
        return Object.values(state.tree.nodes)
            .filter(node => (narrations[node.id]?.status ?? node.narration_status) === 'pending')
            .map(node => node.id);
    }, [state, narrations]);

    const pendingKey = pendingIds.join(',');

    useEffect(() => {
        if (!sessionId || !pendingKey) {
            return;
        }
        const ids = pendingKey.split(',');
        let cancelled = false;
        const timer = setInterval(async () => {
            for (const nodeId of ids) {
                if (cancelled) {
                    return;
                }
                try {
                    const poll = await sandboxService.narration(sessionId, nodeId);
                    if (cancelled) {
                        return;
                    }
                    // `status` here, not `narration_status` - the poll
                    // endpoint and the node shape spell it differently.
                    setNarrations(prev => ({
                        ...prev,
                        [nodeId]: { status: poll.status, narration: poll.narration },
                    }));
                } catch {
                    // A rewound-away node 404s; leave it and let the next
                    // state refresh settle what still exists.
                }
            }
        }, NARRATION_POLL_MS);
        return () => { cancelled = true; clearInterval(timer); };
    }, [sessionId, pendingKey]);

    // ----- evaluation --------------------------------------------------------

    // Keyed on the node rather than the FEN: the node id is what the endpoint
    // echoes back, so a slow reply that lands after the student has moved on
    // can be dropped rather than labelling the new position with the old one's
    // number. Nothing is requested at all while the bar is off.
    const currentNodeId = state?.tree.current_id ?? null;
    useEffect(() => {
        if (!showEval || !sessionId || !currentNodeId) {
            return;
        }
        let cancelled = false;
        (async () => {
            try {
                const result = await sandboxService.evaluate(sessionId);
                if (!cancelled && result.node_id === currentNodeId) {
                    setEvaluation({ score: result.score, mate_in: result.mate_in });
                }
            } catch {
                // An optional readout on a position the student can already
                // see. The bar holds its last value rather than throwing an
                // error strip over a working board.
            }
        })();
        return () => { cancelled = true; };
    }, [showEval, sessionId, currentNodeId]);

    // ----- board / position --------------------------------------------------

    const board = useMemo(() => (state ? new Chess(state.fen) : null), [state]);

    // The same reading of the position the real game and Post-Mortem use -
    // check, checkmate, stalemate, draw and the checked king's square, from
    // ../boardState.ts. This used to be a second hand-written copy of that
    // logic living here; three copies of one chess rule is three chances for
    // the board, the strip and the end-state layer to contradict each other.
    const boardStatus = useMemo(() => readBoardStatus(state?.fen), [state?.fen]);
    const isGameOver = boardStatus.gameOver;

    // A demonstration that quietly ends in mate, with only a greyed-out button
    // to say so, is the one moment in a line a student most needs called out.
    const alert = useMemo(() => {
        if (boardStatus.end) {
            return {
                kind: boardStatus.end.kind === 'checkmate'
                    ? ('checkmate' as const)
                    : ('stalemate' as const),
                text: boardStatus.end.strip,
            };
        }
        if (boardStatus.inCheck && board) {
            const side = board.turn() === 'w' ? 'White' : 'Black';
            return { kind: 'check' as const, text: `Check - ${side} must respond` };
        }
        return null;
    }, [boardStatus, board]);

    /** Nodes from root to current that actually carry a move, oldest first. */
    const line: SandboxNode[] = useMemo(() => {
        if (!state) {
            return [];
        }
        return state.tree.line
            .map(id => state.tree.nodes[id])
            .filter((node): node is SandboxNode => Boolean(node) && node.move !== null);
    }, [state]);

    // Every move the coach has something to say about becomes a message, once,
    // in the order it was played. "Something to say" is an AI move's own
    // reasoning, or a narration that was asked for (its status is anything but
    // 'none' the moment the move lands) - a human move nobody asked to be
    // narrated says nothing, and the Line tab already lists it. Stepping back
    // and forward does not repeat a move; playing a different move from an
    // earlier position is a new node and gets its own message.
    useEffect(() => {
        if (!sessionId) {
            return;
        }
        const fresh: SandboxTranscriptEntry[] = [];
        line.forEach((node, index) => {
            const key = `${sessionId}:${node.id}`;
            if (coachedRef.current.has(key)) {
                return;
            }
            const status = narrations[node.id]?.status ?? node.narration_status;
            if (!node.explanation && status === 'none') {
                return;
            }
            coachedRef.current.add(key);
            fresh.push({
                kind: 'coach',
                nodeId: node.id,
                ply: index + 1,
                san: node.san ?? '',
                source: node.source,
                explanation: node.explanation,
            });
        });
        if (fresh.length > 0) {
            freezeTranscript(fresh);
        }
    }, [sessionId, line, narrations, freezeTranscript]);

    /**
     * Legal destinations per origin square, from the server's own list.
     *
     * `state.legal_moves` is UCI generated by python-chess for exactly this
     * position, so it is the same authority the server will validate the
     * move against. Re-deriving legality from the local chess.js board would
     * introduce a second opinion that can only ever disagree.
     */
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

    /** Occupied squares, for telling a capture from a quiet move in the hints. */
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

    const interactive = takeover && !busy && !booting && !isGameOver && Boolean(state);

    /**
     * The UCI string for a from/to pair, or null if that isn't a legal move.
     *
     * `legal_moves` spells a promotion out four times - e7e8q/r/b/n - so the
     * bare from+to is simply absent for one, and `piece` says which of the
     * four was chosen. It used to be hardcoded to 'q'; a sandbox that cannot
     * demonstrate an underpromotion cannot demonstrate a whole class of
     * endgame, which is exactly the kind of position this mode is for.
     */
    const uciFor = useCallback((from: string, to: string, piece?: PromotionPiece): string | null => {
        const moves = state?.legal_moves ?? [];
        const plain = `${from}${to}`;
        if (!piece && moves.includes(plain)) {
            return plain;
        }
        const promoting = `${plain}${piece ?? 'q'}`;
        return moves.includes(promoting) ? promoting : null;
    }, [state]);

    // ----- Promotion ---------------------------------------------------------
    //
    // Both ways of moving ask before promoting, as Play and Review now do.
    // See components/PromotionPicker.tsx for why this is not the library's own
    // dialog.
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

    // ----- actions -----------------------------------------------------------

    const runAiMove = useCallback(async (): Promise<boolean> => {
        if (!sessionId) {
            return false;
        }
        thinkingRef.current = true;
        setThinking(true);
        try {
            const next = await sandboxService.aiMove(sessionId);
            absorb(next);
            return true;
        } catch (err) {
            // 409 "This line is already over" is the expected end of an
            // auto-played demonstration, not a failure worth shouting about.
            const message = err instanceof Error ? err.message : 'The AI could not move.';
            if (!/already over/i.test(message)) {
                setError(message);
            }
            return false;
        } finally {
            thinkingRef.current = false;
            setThinking(false);
            setPausing(false);
        }
    }, [sessionId, absorb]);

    // Auto-play walks the AI through both sides. It re-reads the ref each
    // pass so the Pause button takes effect on the next half-move rather
    // than after the whole line.
    const startAutoPlay = useCallback(async () => {
        // Re-entrancy guard. autoPlayRef says whether the loop SHOULD keep
        // going; it does not say whether one is still running. Stop clears it
        // immediately, but the loop only notices after the half-move already
        // in flight lands - so between those two moments a second press
        // started a SECOND while-loop while the first was still awaiting its
        // move, and both then called runAiMove. Measured: five rapid presses
        // produced nine ai-move requests and tripped the rate limiter.
        //
        // A press during the unwind is dropped rather than queued. The UI
        // already says what is happening - the button is disabled and the
        // status reads "stopping after this move" - so there is nothing here
        // for the user to be surprised by.
        if (autoPlayLoopRef.current) {
            return;
        }
        autoPlayLoopRef.current = true;
        setError(null);
        setPausing(false);
        autoPlayRef.current = true;
        setAutoPlay(true);
        setBusy(true);
        try {
            while (autoPlayRef.current) {
                const ok = await runAiMove();
                // Re-read the ref before sleeping, not just at the top: Pause
                // pressed during a half-move otherwise still sat out the full
                // inter-move gap after that move landed, leaving the controls
                // disabled for another 650ms with nothing left to wait for.
                if (!ok || !autoPlayRef.current) {
                    break;
                }
                await new Promise(resolve => setTimeout(resolve, AUTOPLAY_GAP_MS));
            }
        } finally {
            // finally, not after the loop: runAiMove can throw, and before
            // this a throw left autoPlay stuck on with the controls disabled
            // and no loop running to clear them.
            autoPlayRef.current = false;
            autoPlayLoopRef.current = false;
            setAutoPlay(false);
            setBusy(false);
        }
    }, [runAiMove]);

    const stopAutoPlay = useCallback(() => {
        autoPlayRef.current = false;
        setAutoPlay(false);
        // Pause takes effect on the *next* half-move, so if one is already
        // out the controls stay disabled until it lands - up to ~14s on
        // Gemini's long tail. Say which of the two is happening.
        if (thinkingRef.current) {
            setPausing(true);
        }
    }, []);

    const navigate = useCallback(async (fn: (id: string) => Promise<SandboxState>) => {
        if (!sessionId) {
            return;
        }
        stopAutoPlay();
        setError(null);
        setBusy(true);
        clearSelection();
        try {
            absorb(await fn(sessionId));
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Navigation failed.');
        } finally {
            setBusy(false);
        }
    }, [sessionId, absorb, stopAutoPlay, clearSelection]);

    /**
     * The student takes over and plays a move.
     *
     * Per decision 2 this branches permanently: the tree either re-walks an
     * existing child (replaying a known line doesn't grow it) or creates a
     * sibling, and the AI's original continuation stays reachable by /goto.
     * That is the whole reason the Line panel is worth having.
     */
    const playUserMove = useCallback(async (uci: string) => {
        if (!sessionId) {
            return;
        }
        stopAutoPlay();
        setError(null);
        setBusy(true);
        clearSelection();
        try {
            // A profile practice session grades its FIRST move against the
            // engine's move from the original game, before the board moves
            // on. Everything after that is ordinary Learn.
            const practice = state?.practice;
            if (practice && !practice.attempted && state?.current_id === state?.tree.root_id) {
                try {
                    const result = await profileService.practiceAttempt(sessionId, uci);
                    setState(prev => prev ? { ...prev, practice: { ...practice, attempted: true, result } } : prev);
                } catch {
                    // Grading is a courtesy; the move still plays.
                }
            }
            absorb(await sandboxService.playMove(sessionId, uci, narrateMine));
        } catch (err) {
            setError(err instanceof Error ? err.message : 'That move was not accepted.');
        } finally {
            setBusy(false);
        }
    }, [sessionId, absorb, stopAutoPlay, clearSelection, narrateMine, state]);

    const onSquareClick = useCallback((square: Square) => {
        if (!interactive) {
            return;
        }
        if (selectedSquare === square) {
            clearSelection();
            return;
        }
        if (selectedSquare) {
            const uci = uciFor(selectedSquare, square);
            if (uci) {
                clearSelection();
                if (!askPromotion(selectedSquare, square)) {
                    void playUserMove(uci);
                }
                return;
            }
        }
        // Selecting a piece with no legal move would light up an empty set
        // of hints, which reads as a broken board rather than as a pinned
        // piece; leave the previous selection cleared instead.
        setSelectedSquare(legalTargets.has(square) ? square : null);
    }, [interactive, selectedSquare, clearSelection, uciFor, askPromotion, playUserMove, legalTargets]);

    const onPieceDrop = useCallback((from: Square, to: Square): boolean => {
        if (!interactive) {
            return false;
        }
        const uci = uciFor(from, to);
        if (!uci) {
            return false;
        }
        // A dropped promotion opens the picker and returns false, which snaps
        // the pawn home while the question is up. That is honest: the move has
        // not been played, so the board should not show it played.
        if (askPromotion(from, to)) {
            return false;
        }
        void playUserMove(uci);
        return true;
    }, [interactive, uciFor, playUserMove, askPromotion]);

    /**
     * Square hints in the app's own amber / green / red rather than the ice
     * accent. Ice is the AI's voice and narration is the only thing that gets
     * it - a highlight under the student's own finger is the one place it
     * would be actively misleading.
     *
     * The values come from --sq-* in styles/obsidian.css, which defines them
     * per theme, rather than from the three hardcoded hexes that used to be
     * here. Those were tuned for the near-black room and stayed at that
     * luminance on paper, where the board is much lighter and a 0.8-opacity
     * wash of #10b981 over #dbe3ec is far weaker than the same wash over
     * #5d6b7d. A custom property resolves inside an inline style, so the
     * hints re-tune themselves on a theme switch with no re-render.
     */
    const squareStyles = useMemo(() => {
        const styles: Record<string, CSSProperties> = {};
        // The checked king is marked whether or not the student has taken
        // over: it is a fact about the position, not about the interaction.
        // Drawn first so a selection or capture hint on the same square wins.
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
    }, [interactive, selectedSquare, legalTargets, occupied, boardStatus.checkedKingSquare]);

    /**
     * Build a position from a description and put the board on it.
     *
     * The transcript is deliberately NOT cleared. It used to be, on the
     * reasoning that a new scenario is a new subject - which was right while
     * the builder was a separate bar at the top of the screen. Now that the
     * builder IS the chat, clearing it would delete the very message that
     * asked for the position, so the conversation continues across a rule.
     *
     * The SERVER's transcript does start empty, because /scenario opens a new
     * session, and that divergence is deliberate: the server's copy exists to
     * be replayed to Gemini, and replaying questions about a position that is
     * no longer on the board would make the coach worse rather than better.
     * The reader keeps the history; the model gets a clean slate.
     */
    const buildScenario = useCallback(async (text: string) => {
        stopAutoPlay();
        setError(null);
        setGenerating(true);
        try {
            const next = await sandboxService.createScenario(text);
            // The outgoing session is NOT deleted here. The effect keyed on
            // sessionId already drops it in its cleanup the moment the id
            // changes, which is precisely what opening a scenario does -
            // deleting it here as well meant two calls for one session, one of
            // which always lost and came back 404. Harmless, caught, and still
            // logged by the browser as a failed request on every build.
            setNarrations({});
            setChatError(null);
            setProfileDraft(null);
            clearSelection();
            // A scenario names the side it was built for ("as white"), and
            // that side is whoever is to move in the position it produced.
            setOrientation(next.turn);
            setBrief(next.scenario
                ? {
                    description: next.scenario.description,
                    notes: next.scenario.notes || null,
                    favorMet: next.scenario.favor_met ?? null,
                }
                : null);
            absorb(next);
            freezeTranscript([{
                kind: 'divider',
                text: next.scenario?.description ?? 'New position',
            }]);
            startFreshHistory();
            return true;
        } catch (err) {
            // Scenario failures are deliberately honest - an opening the book
            // doesn't have, or material that can only ever draw, comes back as
            // a 400 that says which. It is shown in the conversation that
            // asked for it, in the coach's own bubble, rather than in a strip
            // at the top of a screen the reader is no longer looking at.
            const detail = err instanceof Error ? err.message : 'Could not build that position.';
            freezeTranscript([{ kind: 'turn', role: 'model', text: detail }]);
            return false;
        } finally {
            setGenerating(false);
        }
    }, [absorb, stopAutoPlay, clearSelection, freezeTranscript, startFreshHistory]);

    // ----- opponent profile --------------------------------------------------

    const sessionProfile = state?.opponent_profile ?? DEFAULT_PROFILE_ID;
    const profileValue = profileDraft ?? sessionProfile;
    const profileDirty = profileValue !== sessionProfile;

    /**
     * Put every piece back on its starting square.
     *
     * The only reset there is. It used to sit beside "Restart line", which
     * returned to whatever position the SESSION began at - so on a generated
     * endgame the two buttons did visibly different things, and on a normal
     * board they did exactly the same thing, which is the worst of both. One
     * button with one meaning: the pieces go home, always.
     *
     * The scenario is abandoned with it, because the session no longer starts
     * where the scenario put it - the description would otherwise caption a
     * position it no longer describes. A rule in the transcript marks the
     * change, and the position can be rebuilt by asking for it again, which is
     * now a sentence in the chat rather than a lost button.
     *
     * The profile comes along if it was staged, so changing the dropdown and
     * resetting is one action rather than two.
     */
    const handleResetBoard = useCallback(async () => {
        if (!sessionId) {
            return;
        }
        stopAutoPlay();
        setError(null);
        setBusy(true);
        try {
            const next = await sandboxService.resetToStandard(
                sessionId,
                profileDirty ? profileValue : undefined,
            );
            setNarrations({});
            setProfileDraft(null);
            clearSelection();
            setBrief(null);
            setOrientation(next.turn);
            absorb(next);
            freezeTranscript([{ kind: 'divider', text: 'Board reset to the starting position' }]);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Could not reset the board.');
        } finally {
            setBusy(false);
        }
    }, [sessionId, profileDirty, profileValue, absorb, stopAutoPlay, clearSelection, freezeTranscript]);

    // ----- alternatives ------------------------------------------------------
    // ----- the move tree -----------------------------------------------------

    /**
     * The whole tree, PGN-style: the main line runs inline and every other
     * continuation from the same position is an indented variation beneath
     * the move it replaces.
     *
     * "Main line" here means the *first* child, which is the one that was
     * played from that position first - so an AI demonstration stays the
     * spine of the display and the student's own detours hang off it, which
     * is the way round that makes a demonstration readable. Nothing is ever
     * deleted from the tree server-side, so an abandoned branch is still
     * here to be clicked back into.
     */
    const treeView = useMemo((): ReactNode => {
        if (!state) {
            return null;
        }
        const nodes = state.tree.nodes;
        const onPath = new Set(state.tree.line);

        const chip = (node: SandboxNode, withNumber: boolean) => {
            const parent = node.parent_id ? nodes[node.parent_id] : undefined;
            const entry = narrations[node.id] ?? {
                status: node.narration_status,
                narration: node.narration,
            };
            const classes = [
                'sandbox-tree-move',
                `sandbox-tree-${node.source}`,
                node.id === state.tree.current_id ? 'is-current' : '',
                onPath.has(node.id) ? 'on-path' : '',
            ].filter(Boolean).join(' ');
            return (
                <button
                    key={node.id}
                    type="button"
                    className={classes}
                    disabled={busy}
                    title={node.explanation ?? undefined}
                    onClick={() => void navigate(id => sandboxService.goto(id, node.id))}
                >
                    {(withNumber || node.mover === 'white') && (
                        <span className="sandbox-tree-num">{moveNumber(node, parent)}</span>
                    )}
                    <span className="sandbox-tree-san">{node.san}</span>
                    {entry.status === 'ready' && <span className="sandbox-tree-dot" aria-hidden="true" />}
                </button>
            );
        };

        // Walk down first-children inline; peel every other child off as a
        // nested block. Recursion depth is the depth of the tree, which is
        // the length of the longest line explored - not a concern here.
        const continueFrom = (parentId: string): ReactNode[] => {
            const out: ReactNode[] = [];
            let parent: SandboxNode | undefined = nodes[parentId];
            // The first chip of a run always prints its number; after that
            // only White's does, until a variation block interrupts the row
            // and the next move needs re-anchoring.
            let needsNumber = true;
            while (parent && parent.children.length > 0) {
                // Annotated rather than destructured-and-inferred: `parent`
                // is reassigned to `main` at the bottom of the loop, so
                // letting TS infer these makes mainId depend on itself
                // (TS7022) even though the runtime types are obvious.
                const mainId: string = parent.children[0];
                const altIds: string[] = parent.children.slice(1);
                const main: SandboxNode | undefined = nodes[mainId];
                if (!main) {
                    break;
                }
                out.push(chip(main, needsNumber));
                needsNumber = false;
                for (const altId of altIds) {
                    const alt = nodes[altId];
                    if (!alt) {
                        continue;
                    }
                    out.push(
                        <div className="sandbox-tree-variation" key={`var-${altId}`}>
                            {chip(alt, true)}
                            {continueFrom(altId)}
                        </div>,
                    );
                    needsNumber = true;
                }
                parent = main;
            }
            return out;
        };

        return (
            <div className="sandbox-tree">
                <button
                    type="button"
                    className={`sandbox-tree-move sandbox-tree-start ${
                        state.tree.current_id === state.tree.root_id ? 'is-current' : ''
                    }`}
                    disabled={busy}
                    onClick={() => void navigate(id => sandboxService.goto(id, state.tree.root_id))}
                >
                    Start
                </button>
                {continueFrom(state.tree.root_id)}
            </div>
        );
    }, [state, narrations, busy, navigate]);

    /** Ask the coach a question about the position on the board. */
    const askCoach = useCallback(async (message: string) => {
        if (!sessionId) {
            return;
        }
        setChatError(null);
        setChatSending(true);
        try {
            const result = await sandboxService.chat(sessionId, message);
            // The server's transcript wins for the turns it owns - it is the
            // copy Gemini is replayed on - and `absorbed` keeps the local
            // dividers around it from being replaced along with them.
            setChat(prev => ({ ...prev, live: result.history }));
        } catch (err) {
            // Put the question back in the box so it can be retried without
            // being retyped, and drop the optimistic turn: it never landed.
            setChat(prev => ({ ...prev, live: prev.live.slice(0, -1) }));
            setChatInput(message);
            setChatError(err instanceof Error ? err.message : 'The coach could not answer.');
        } finally {
            setChatSending(false);
        }
    }, [sessionId]);

    /**
     * One composer, two jobs.
     *
     * Everything typed here goes to the classifier first, because the only
     * reliable way to tell "give me something easier" from "why was that
     * easier for white?" is to read the sentence. A question is answered in
     * place. A build request is acted on immediately when the board is still
     * untouched, and proposed rather than performed when it is not - building
     * opens a new session and drops the move tree, and there is no undo, so
     * the one thing this must never do is quietly throw away a line someone
     * is in the middle of studying.
     *
     * The classifier itself is biased toward "ask" and answers "ask" whenever
     * it is unavailable, so the failure modes all land on the harmless side.
     */
    const sendComposer = useCallback(async () => {
        const message = chatInput.trim();
        if (!message || !sessionId || chatSending || generating) {
            return;
        }
        setChatInput('');
        setChatError(null);
        setPendingBuild(null);
        // Show the message immediately: classification plus a reply can take
        // several seconds, and an empty box with nothing new in the log looks
        // like the send did not happen.
        setChat(prev => ({ ...prev, live: [...prev.live, { role: 'user', text: message }] }));
        setChatSending(true);

        let intent: 'ask' | 'build' = 'ask';
        if (looksPasted(message)) {
            // A pasted FEN or PGN skips the classifier entirely.
            //
            // The classifier is deliberately biased toward ASK (see §8.5: the
            // asymmetry of the two wrong answers is the whole design), and a
            // wall of notation is exactly the kind of input it hedges on. But
            // nobody pastes a FEN in order to be told about it - a position is
            // a thing you hand over to be set up, and the honest-failure
            // message this sprint added tells people to do precisely that, so
            // it has to be the one input that cannot be misread.
            //
            // The server re-checks with its own parser (scenario_service's
            // looks_like_fen / looks_like_pgn) and this only decides routing,
            // so a disagreement between the two costs a round trip and never a
            // wrong board.
            intent = 'build';
        } else {
            try {
                ({ intent } = await sandboxService.classify(message));
            } catch {
                // Classification is best-effort by design; the endpoint answers
                // "ask" rather than erroring, so reaching here means the network
                // failed and "ask" is still the safe reading.
            }
        }

        if (intent === 'ask') {
            await askCoach(message);
            return;
        }

        setChatSending(false);
        if (line.length === 0) {
            // Nothing to lose: no moves have been played, so there is no line
            // to replace and asking would be ceremony.
            await buildScenario(message);
            return;
        }
        setPendingBuild(message);
    }, [chatInput, sessionId, chatSending, generating, askCoach, line.length, buildScenario]);

    const confirmBuild = useCallback(async () => {
        const text = pendingBuild;
        if (!text) {
            return;
        }
        setPendingBuild(null);
        await buildScenario(text);
    }, [pendingBuild, buildScenario]);

    const declineBuild = useCallback(() => {
        const text = pendingBuild;
        setPendingBuild(null);
        if (!text) {
            return;
        }
        // Recorded rather than silently dismissed, so the log still shows what
        // was asked for and that it was left alone.
        freezeTranscript([{
            kind: 'confirm',
            text: 'Left the line as it was.',
            prompt: text,
            resolved: 'declined',
        }]);
    }, [pendingBuild, freezeTranscript]);

    // Keep the newest turn in view. Runs on the pending state too, so the
    // "Thinking..." line is what you are left looking at.
    useEffect(() => {
        const log = chatLogRef.current;
        if (log) {
            log.scrollTop = log.scrollHeight;
        }
    }, [transcript, chatSending, pendingBuild]);

    // ----- keyboard navigation -----------------------------------------------

    // Stepping through a line is the single most repeated action in this mode,
    // and until now it could only be done by clicking one of two buttons. Arrow
    // keys are what anyone who has used a chess study interface will reach for
    // first, so they drive the same navigate() the buttons do rather than a
    // parallel path that could disagree with them.
    //
    // Bound on the document because the thing being navigated is the position,
    // not a focused widget - requiring the user to first tab into the move tree
    // to step through the line would be the kind of ceremony that stops people
    // using the shortcut at all.
    useEffect(() => {
        const onKeyDown = (event: KeyboardEvent) => {
            // Never steal a keystroke from someone typing a scenario prompt, and
            // leave modified keys to the browser (Cmd/Ctrl+Left is "go back").
            const target = event.target as HTMLElement | null;
            if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) {
                return;
            }
            if (event.metaKey || event.ctrlKey || event.altKey) {
                return;
            }
            // Busy means a half-move is still landing. Queueing navigation on
            // top of it would race the request that is already in flight.
            if (busy || booting || !sessionId) {
                return;
            }

            switch (event.key) {
                case 'ArrowLeft':
                    if (line.length === 0) return;
                    event.preventDefault();
                    void navigate(id => sandboxService.back(id));
                    break;
                case 'ArrowRight':
                    event.preventDefault();
                    void navigate(id => sandboxService.forward(id));
                    break;
                case 'Home':
                    if (!state) return;
                    event.preventDefault();
                    void navigate(id => sandboxService.goto(id, state.tree.root_id));
                    break;
                default:
            }
        };
        document.addEventListener('keydown', onKeyDown);
        return () => document.removeEventListener('keydown', onKeyDown);
    }, [busy, booting, sessionId, line.length, state, navigate]);

    // ----- derived UI state --------------------------------------------------

    // What used to be four dead buttons and no explanation. `thinking ||
    // autoPlay` rather than `thinking` alone on purpose: auto-play leaves a
    // 650ms gap between half-moves, and reading `thinking` directly made the
    // label flicker between two strings on every single ply.
    const statusText = booting
        ? 'Opening a sandbox...'
        : pausing
            ? 'Finishing this move...'
            : thinking || autoPlay
                ? 'AI is thinking...'
                : busy
                    ? 'Working...'
                    : null;

    const panelMeta = PANELS.find(p => p.id === panel) ?? PANELS[0];

    // ----- render ------------------------------------------------------------

    return (
        <div
            className="sandbox"
            // Published so Sandbox.css can size the board's frame from the
            // same number the board itself is rendered at.
            style={{ ['--board-size' as string]: `${boardSize}px` } as CSSProperties}
        >
            {/* The whole top strip is gone. It held a scenario prompt bar,
                which is now the chat composer, and a title, which is now the
                heading of the panel that talks about the position. What is
                left is the way out, and that belongs next to the panel rather
                than above a board it has nothing to do with.

                Decision 3 still holds: setup is natural language only, and
                there are still no preset buttons anywhere. */}
            {error && <div className="sandbox-error fade-slide-in">{error}</div>}

            <div className="sandbox-body">
                <div className="sandbox-board-column" ref={boardColumnRef}>
                    <div className={`sandbox-board-wrapper ${busy ? 'sandbox-thinking' : ''}`}>
                        {booting ? (
                            <div className="sandbox-booting">Opening a sandbox...</div>
                        ) : (
                            state && (
                                <Chessboard
                                    position={state.fen}
                                    boardWidth={boardSize}
                                    boardOrientation={orientation}
                                    arePiecesDraggable={interactive}
                                    isDraggablePiece={({ sourceSquare }) => legalTargets.has(sourceSquare)}
                                    onPieceDrop={onPieceDrop}
                                    // Kept ON even though this mode no longer
                                    // auto-queens. Its only job here is to stop
                                    // react-chessboard opening ITS own dialog
                                    // on a drag to the last rank; the choice is
                                    // made by our picker instead, on both the
                                    // drag and the click path, so the same move
                                    // asks the same question either way. See
                                    // PromotionPicker.tsx and trap 13.
                                    autoPromoteToQueen
                                    onSquareClick={onSquareClick}
                                    customSquareStyles={squareStyles}
                                    animationDuration={150}
                                    customPieces={customPieces}
                                    customNotationStyle={BOARD_NOTATION_STYLE}
                                    // Theme tokens, matching the real game's
                                    // board - a CSS custom property resolves in
                                    // an inline style, so both boards recolour
                                    // together on a theme switch.
                                    customDarkSquareStyle={{ backgroundColor: 'var(--board-dark)' }}
                                    customLightSquareStyle={{ backgroundColor: 'var(--board-light)' }}
                                />
                            )
                        )}
                        {/* Inside the board frame, never the page - see
                            BoardEndState.tsx. Its reset is this mode's own
                            reset under this mode's own label, so the action
                            keeps one name. */}
                        <BoardEndState
                            end={boardStatus.end}
                            onReset={() => void handleResetBoard()}
                            resetLabel="Reset board"
                        />
                        {/* Asked on the promotion square, inside the board
                            frame, so it cannot reach the coach panel or the
                            controls at any width. */}
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
                                if (uci) void playUserMove(uci);
                            }}
                            onCancel={() => setPendingPromotion(null)}
                        />
                        {/* Beside the board, the board's height, in the slot
                            the column keeps for it - see EvalBar. Nothing is
                            requested while it is off, so the bar shows the
                            midpoint until the first evaluation lands. */}
                        <EvalBar
                            share={evaluation ? evalShare(evaluation) : 0.5}
                            label={evaluation ? evalLabel(evaluation) : '0.0'}
                            on={showEval}
                            flipped={orientation === 'black'}
                        />
                    </div>

                    {/* One AI control, not two. "AI move" and "Play line" did
                        the same thing at two granularities and the pair had to
                        be read to tell which; this starts the demonstration and
                        stops it, and says which it will do next in its own
                        label. "Take over" takes the slot the second button had,
                        because the thing worth discovering there is that you can
                        play too - which was previously buried in the row below,
                        below the status line, next to a checkbox. */}
                    {/* The same three announcements the real game makes, in
                        the same colours. It sits between the board and the
                        controls, and it is always in the DOM so that a line
                        arriving at mate does not shove every button down a row
                        at the moment you are reaching for one. */}
                    {/* The alert, always in the layout - quiet rather than
                        absent, so arriving at mate does not shove the
                        transport down a row. The eval bar that shared this
                        line is beside the board now (EvalBar, inside the
                        frame above), in the slot the column keeps for it. */}
                    <div className="sandbox-strip">
                    <div className={`sandbox-alert ${alert ? `is-${alert.kind}` : 'is-quiet'}`} role="status" aria-live="polite">
                        {alert?.text ?? ''}
                    </div>
                    </div>

                    <div className="sandbox-controls">
                        <button
                            className={`action-btn ${autoPlay ? 'pause-btn' : 'ai-move-btn'}`}
                            onClick={() => (autoPlay ? stopAutoPlay() : void startAutoPlay())}
                            disabled={(busy && !autoPlay) || booting || isGameOver || !state}
                            aria-pressed={autoPlay}
                            title={autoPlay
                                ? 'Stop after the half-move currently being played'
                                : 'Let the AI play both sides from here'}
                        >
                            {autoPlay ? 'Stop' : 'AI move'}
                        </button>
                        <button
                            type="button"
                            className={`action-btn sandbox-takeover-btn ${takeover ? 'is-on' : ''}`}
                            onClick={() => {
                                setTakeover(!takeover);
                                clearSelection();
                            }}
                            disabled={booting || !state}
                            aria-pressed={takeover}
                            title={takeover
                                ? 'Stop playing; the board goes back to being a demonstration'
                                : 'Play the moves yourself from this position'}
                        >
                            {takeover ? 'Playing' : 'Take over'}
                        </button>
                        <button
                            className="action-btn sandbox-nav-btn"
                            onClick={() => void navigate(id => sandboxService.back(id))}
                            disabled={busy || booting || line.length === 0}
                            title="Back one half-move (Left arrow)"
                        >
                            Back
                        </button>
                        <button
                            className="action-btn sandbox-nav-btn"
                            onClick={() => void navigate(id => sandboxService.forward(id))}
                            /* There has to be somewhere to go. `Back` has
                               always guarded on the line being empty and this
                               one guarded on nothing, so at the root of a
                               fresh session - the first thing anyone sees in
                               this mode - Forward sat there enabled, and
                               pressing it spent a round trip arriving at the
                               position it was already on. `MoveTree.forward()`
                               no-ops when the current node has no children, so
                               the button was never wrong about the chess; it
                               was wrong about offering. Post-Mortem already
                               states the rule this follows: a control that
                               does nothing is worse than one that is not
                               there. */
                            disabled={busy || booting || !state || state.node.children.length === 0}
                            title="Forward one half-move (Right arrow)"
                        >
                            Forward
                        </button>
                    </div>

                    {/* The second row of the transport: put the board back, and
                        turn it round. The same shape as Play's "Make AI move /
                        Play as Black" - two buttons sharing the board's width -
                        because they are the same kind of thing: things you do
                        to the board while working, not settings. The display
                        switches (eval bar, coach my moves), the board size and
                        the piece set are all in the Actions tab now, so this
                        row is two buttons and not a wrapping strip of eight
                        controls. */}
                    <div className="sandbox-controls sandbox-controls-secondary">
                        {/* One reset. It takes the staged profile with it,
                            so changing the dropdown and starting again is one
                            action - which is what the old "Restart at 18" label
                            was for, on a button that no longer exists. */}
                        <button
                            type="button"
                            className={`action-btn sandbox-reset-btn ${profileDirty ? 'is-on' : ''}`}
                            onClick={() => void handleResetBoard()}
                            disabled={busy || booting || !state}
                            title="Put every piece back on its starting square"
                        >
                            {profileDirty
                                ? `Reset at ${profileLabel(profileValue)}`
                                : 'Reset board'}
                        </button>
                        {/* Rotate. The sandbox already carried an
                            `orientation` state and drew the board from it -
                            there was simply never a control that changed it,
                            so it was White's view forever. A student studying
                            a Black-side defence was reading the position
                            upside down. It is a toggle rather than a pair of
                            radio buttons because there are exactly two answers
                            and the label can say which one you would get. */}
                        <button
                            type="button"
                            className="action-btn sandbox-rotate-btn"
                            onClick={() => setOrientation(o => (o === 'white' ? 'black' : 'white'))}
                            title={orientation === 'white'
                                ? 'Turn the board round and look at it from Black’s side'
                                : 'Turn the board round and look at it from White’s side'}
                        >
                            {orientation === 'white' ? 'View as Black' : 'View as White'}
                        </button>
                    </div>

                    {/* Always rendered so the column doesn't jump by a line
                        height every time the AI starts or stops thinking, and
                        announced politely because this is the only thing on
                        screen that explains a wait which runs to ~14s on
                        Gemini's long tail. */}
                    <div
                        className={`sandbox-status ${statusText ? 'is-busy' : ''}`}
                        role="status"
                        aria-live="polite"
                    >
                        {statusText ?? ''}
                    </div>

                    {/* The one setting that changes how the demonstration is
                        driven, on the quiet row under the transport - the same
                        row and the same select Play uses. Alone here, it gets
                        its name back. */}
                    <div className="ws-meta sandbox-meta-row">
                        <span className="ws-meta-spacer" />
                        <label className="sandbox-difficulty">
                            <span className="ws-label-full">Opponent level</span>
                            <select
                                aria-label="Engine strength"
                                value={profileValue}
                                onChange={event => setProfileDraft(event.target.value)}
                                disabled={busy || booting || !state}
                            >
                                {OPPONENT_PROFILES.map(p => (
                                    <option key={p.id} value={p.id}>
                                        {profileLabel(p.id)}
                                    </option>
                                ))}
                            </select>
                        </label>
                    </div>

                    {/* ALWAYS in the layout, hidden with `visibility` rather
                        than removed - the rule §11 states for the eval row,
                        for exactly the reason seen here.

                        This was `{takeover && ...}`, so pressing Take over
                        added a row to the column, and the board fitter answered
                        by shrinking the board. Measured at 1440x900: the hint
                        grew the column by 17px and the board lost 41px. That
                        amplification is the whole point of §11's warning - a
                        19px strip once cost 70px of board the same way - and it
                        reads as "taking over minimises the board", which is
                        what a tester reported.

                        `aria-hidden` follows the visibility so a screen reader
                        is not told about advice that is not on screen. */}
                    <p className="sandbox-hint" aria-hidden={!takeover}>
                        {takeover && (
                            <>
                                Your move branches the line permanently - the AI's own continuation
                                stays in the tree, under <strong>Line</strong>.
                            </>
                        )}
                    </p>

                    {/* Whose move it is now sits under the title, where it is
                        read once. This row is the line so far and nothing
                        else - it used to repeat the turn in a pill beside it,
                        which made two different-looking chips say the same
                        thing as the player strip already did. */}
                    {state?.line_san.length ? (
                        <div className="sandbox-meta">
                            <span className="sandbox-meta-item sandbox-meta-line">
                                {state.line_san.join(' ')}
                            </span>
                        </div>
                    ) : null}
                </div>

                {/* Above the BOARD, not above the panel. It names the
                    position and says what the position is for, so it belongs
                    with the position - reading the instruction and then looking
                    at the pieces should not be a trip across the layout. It
                    still occupies its own grid row, so the board and the tab
                    row beside it go on starting at the same line. */}
                <div className="sandbox-identity">
                    <div className="sandbox-identity-row">
                        {/* The session's own short name, not the scenario's
                            description. scenario_service writes a title like
                            "Hard Rook Endgame" and a description that is a
                            full sentence about what to do in it; the sentence
                            ran to three lines as a heading and pushed
                            everything under it down. The description is on the
                            line below, and on the rule in the transcript where
                            the position began. */}
                        <h2 className="sandbox-title" title={brief?.description ?? undefined}>
                            {state?.title ?? 'Learner Mode'}
                        </h2>
                        {/* No "Back to game" here any more. It was a one-way
                            link labelled with its DESTINATION - the very
                            pattern the header's segmented control replaced -
                            and it duplicated a control that is on screen at
                            all times and also says which mode you are in.
                            Review's slot holds "Close game", which destroys
                            the imported game and has no equivalent in the
                            header; that is what this slot is for. */}
                    </div>
                    <span className="sandbox-subtitle">
                        {booting
                            ? 'Opening a board'
                            : isGameOver
                                ? 'This line is finished'
                                : state
                                    ? `${state.turn === 'white' ? 'White' : 'Black'} to move`
                                    : 'No board yet'}
                        {brief && !state?.practice ? ` - ${brief.description}` : ''}
                    </span>
                    {/* The correction, when there is one.

                        `notes` is the only thing that has actually been
                        CHECKED against the position on the board. The
                        description beside it was written by the model before
                        the position existed, so it can promise a win that was
                        never built: asking for "a position where black is
                        completely winning" produced a lone knight against a
                        queen and two rooks, told the student to "find the
                        winning plan for black", and kept the note saying black
                        was 506 centipawns down to itself. The note was already
                        computed, already held in state, and simply never
                        rendered. */}
                    {brief?.notes && (
                        <span
                            className={`sandbox-note ${brief.favorMet === false ? 'is-warn' : ''}`}
                            role="status"
                        >
                            {brief.notes}
                        </span>
                    )}
                    {state?.practice && (
                        <div className="sandbox-practice" data-testid="sandbox-practice" role="status">
                            <span>
                                <strong>This position comes from one of your games.</strong>
                                {' '}{state.practice.attempted ? 'Your first move has been graded; the coach plays on from here.' : state.practice.instructions}
                            </span>
                            <span className="sandbox-practice-source">
                                {state.practice.game_label ?? `game #${state.practice.game_id}`}
                                {' · '}Move {state.practice.move_number}: you played {state.practice.played_san}
                                {state.practice.result?.best_san ? `; engine preferred ${state.practice.result.best_san}.` : '.'}
                            </span>
                            {state.practice.result && (
                                <span className={`sandbox-practice-result ${state.practice.result.passed ? 'is-pass' : 'is-miss'}`} data-testid="sandbox-practice-result">
                                    {state.practice.result.passed
                                        ? `You found ${state.practice.result.best_san} - the engine's move.`
                                        : state.practice.result.repeated_mistake
                                            ? `You played ${state.practice.result.played_san} again. The engine preferred ${state.practice.result.best_san}.`
                                            : `You played ${state.practice.result.played_san}; the engine preferred ${state.practice.result.best_san}.`}
                                </span>
                            )}
                            {state.practice.attempted && (
                                <span className="sandbox-practice-next">
                                    Keep playing the line here, or{' '}
                                    <a href="/profile">go back to your profile</a> for another position.
                                </span>
                            )}
                        </div>
                    )}
                </div>

                <div className="sandbox-canvas">
                    <div className="sandbox-canvas-header">
                        <div className="sandbox-tabs" role="tablist" aria-label="Panel">
                            {PANELS.map(tab => (
                                <button
                                    key={tab.id}
                                    type="button"
                                    role="tab"
                                    id={`sandbox-tab-${tab.id}`}
                                    aria-controls="sandbox-panel"
                                    aria-selected={panel === tab.id}
                                    className={`sandbox-tab ${panel === tab.id ? 'is-active' : ''}`}
                                    onClick={() => setPanel(tab.id)}
                                >
                                    {tab.label}
                                </button>
                            ))}
                        </div>
                        <span className="sandbox-canvas-sub">{panelMeta.sub}</span>
                    </div>

                    <div
                        className="sandbox-canvas-inner"
                        id="sandbox-panel"
                        role="tabpanel"
                        aria-labelledby={`sandbox-tab-${panel}`}
                        tabIndex={0}
                    >
                        {panel === 'tree' && (
                            <>
                                {treeView}
                                {line.length === 0 && (
                                    <EmptyState title="Nothing played yet">
                                        Every move - the coach's and yours - lands
                                        here, and nothing is ever removed, so you
                                        can always rewind into a line you left.
                                    </EmptyState>
                                )}
                            </>
                        )}

                        {panel === 'actions' && (
                            <div className="actions-list">
                                <label className="game-switch actions-item">
                                    <input
                                        type="checkbox"
                                        checked={showEval}
                                        onChange={event => setShowEval(event.target.checked)}
                                    />
                                    <span>Eval bar</span>
                                    <span className="actions-note">The engine's evaluation under the board. Off by default: it shares the engine with move selection.</span>
                                </label>
                                <label className="game-switch actions-item">
                                    <input
                                        type="checkbox"
                                        checked={narrateMine}
                                        onChange={event => setNarrateMine(event.target.checked)}
                                    />
                                    <span>Coach my moves</span>
                                    {/* Off by default server-side, and left off here
                                        for the same reason: narrating every move a
                                        student tries while exploring doubles the load
                                        on the one shared API key for commentary
                                        nobody asked for. */}
                                    <span className="actions-note">Have the coach comment on the moves you play after taking over, not only the AI's.</span>
                                </label>
                                <div className="actions-item">
                                    <BoardSizeControl value={boardSizePref} onChange={setBoardSizePref} />
                                    <span className="actions-note">How large the board is drawn. Auto follows the window.</span>
                                </div>
                                <CoachStyleSettings />
                                <div className="actions-item">
                                    <button
                                        type="button"
                                        className="action-btn actions-clear-chat"
                                        onClick={() => void clearChat()}
                                        disabled={booting || !sessionId || chatSending || generating || transcript.length === 0}
                                    >
                                        Clear chat
                                    </button>
                                    <span className="actions-note">
                                        Empties the conversation. The coach forgets it too; the board and the line stay.
                                    </span>
                                </div>
                            </div>
                        )}

                        {panel === 'board' && (
                            <div className="sandbox-themes">
                                {/* The swatch is the set's own knight and king,
                                    drawn by the same components the board uses.
                                    The real game's picker shows a board-colour
                                    chip instead, which cannot distinguish these
                                    four at all - getBoardColors returns the same
                                    pair for every one of them, because what
                                    changes between them is the PIECES. Showing
                                    the thing being chosen is the whole job of a
                                    picker. */}
                                {PIECE_THEME_LIST.map(theme => {
                                    const pieces = getCustomPieces(theme.id);
                                    const WhiteKnight = pieces.wN;
                                    const BlackKing = pieces.bK;
                                    const active = pieceTheme === theme.id;
                                    return (
                                        <button
                                            key={theme.id}
                                            type="button"
                                            className={`sandbox-theme ${active ? 'is-active' : ''}`}
                                            aria-pressed={active}
                                            onClick={() => setPieceTheme(theme.id)}
                                        >
                                            <span className="sandbox-theme-pieces" aria-hidden="true">
                                                {WhiteKnight && <WhiteKnight squareWidth={40} />}
                                                {BlackKing && <BlackKing squareWidth={40} />}
                                            </span>
                                            <span className="sandbox-theme-label">{theme.label}</span>
                                        </button>
                                    );
                                })}
                            </div>
                        )}

                        {panel === 'chat' && (
                            <div className="sandbox-chat">
                                <div className="sandbox-chat-log" ref={chatLogRef}>
                                    {transcript.length === 0 && !pendingBuild && (
                                        <EmptyState title="Ask for a position, or ask about this one">
                                            Describe what you want to study - "a hard
                                            rook endgame as white" - and it gets built.
                                            Play a line and the coach talks you through
                                            it here, move by move. Ask a question and it
                                            is answered against the position and the
                                            engine's ranking of every legal move.
                                        </EmptyState>
                                    )}
                                    {transcript.map((entry, index) => {
                                        if (entry.kind === 'divider') {
                                            return (
                                                <div className="sandbox-chat-divider" key={`d-${index}`}>
                                                    <span>{entry.text}</span>
                                                </div>
                                            );
                                        }
                                        if (entry.kind === 'confirm') {
                                            return (
                                                <div
                                                    className="sandbox-chat-msg sandbox-chat-model"
                                                    key={`c-${index}`}
                                                >
                                                    {renderFormattedText(entry.text)}
                                                </div>
                                            );
                                        }
                                        if (entry.kind === 'coach') {
                                            // The same three states the Coach
                                            // tab drew, in a bubble: the move,
                                            // whose it was, the reasoning, and
                                            // the narration as it arrives.
                                            const narration = narrations[entry.nodeId] ?? {
                                                status: state?.tree.nodes[entry.nodeId]?.narration_status ?? 'none',
                                                narration: state?.tree.nodes[entry.nodeId]?.narration ?? null,
                                            };
                                            return (
                                                <article
                                                    key={`m-${entry.nodeId}`}
                                                    className="sandbox-chat-msg sandbox-chat-model sandbox-chat-coach fade-slide-in"
                                                >
                                                    <header className="sandbox-ply-head">
                                                        <span className="sandbox-ply-num">{entry.ply}</span>
                                                        <span className="sandbox-ply-san">{entry.san}</span>
                                                        <span className={`sandbox-ply-source sandbox-ply-${entry.source}`}>
                                                            {entry.source === 'ai' ? 'AI' : 'You'}
                                                        </span>
                                                    </header>
                                                    {entry.explanation && (
                                                        <p className="sandbox-ply-explanation">{entry.explanation}</p>
                                                    )}
                                                    {narration.status === 'pending' && (
                                                        <p className="sandbox-ply-pending">Coach is thinking...</p>
                                                    )}
                                                    {narration.status === 'failed' && (
                                                        <p className="sandbox-ply-failed">
                                                            The coach did not get back in time on this one.
                                                        </p>
                                                    )}
                                                    {narration.status === 'ready' && narration.narration && (
                                                        <p className="sandbox-ply-narration">{narration.narration}</p>
                                                    )}
                                                </article>
                                            );
                                        }
                                        return (
                                            <div
                                                key={`t-${index}`}
                                                className={`sandbox-chat-msg sandbox-chat-${entry.role}`}
                                            >
                                                {/* The coach emphasises moves and
                                                    opening names with **bold**;
                                                    unrendered, those asterisks
                                                    showed up literally in the reply. */}
                                                {entry.role === 'model' ? renderFormattedText(entry.text) : entry.text}
                                            </div>
                                        );
                                    })}
                                    {(chatSending || generating) && (
                                        <div
                                            className="sandbox-chat-msg sandbox-chat-model is-pending"
                                            role="status"
                                            aria-live="polite"
                                        >
                                            {generating ? 'Building the position...' : 'Thinking...'}
                                        </div>
                                    )}
                                    {/* Asked, not done. Building opens a new
                                        session and drops the move tree, and
                                        there is no undo - so a line someone is
                                        part-way through is never replaced
                                        without them saying so. */}
                                    {pendingBuild && (
                                        <div
                                            className="sandbox-chat-msg sandbox-chat-model is-pending sandbox-chat-confirm"
                                            role="alertdialog"
                                            aria-label="Replace the current line?"
                                        >
                                            <p className="sandbox-chat-confirm-text">
                                                I can set that up, but it replaces the line
                                                you're on - {line.length}{' '}
                                                {line.length === 1 ? 'move' : 'moves'} and any
                                                branches off it.
                                            </p>
                                            <div className="sandbox-chat-confirm-actions">
                                                <button
                                                    type="button"
                                                    className="action-btn sandbox-chat-send"
                                                    onClick={() => void confirmBuild()}
                                                >
                                                    Build it
                                                </button>
                                                <button
                                                    type="button"
                                                    className="sandbox-toggle"
                                                    onClick={declineBuild}
                                                >
                                                    Never mind
                                                </button>
                                            </div>
                                        </div>
                                    )}
                                </div>

                                {chatError && (
                                    <p className="sandbox-chat-error" role="alert">{chatError}</p>
                                )}

                                <form
                                    className="sandbox-chat-row"
                                    onSubmit={event => {
                                        event.preventDefault();
                                        void sendComposer();
                                    }}
                                >
                                    <input
                                        className="sandbox-chat-input"
                                        value={chatInput}
                                        onChange={event => setChatInput(event.target.value)}
                                        /* The third thing this box takes is now named. Pasting a
                                           FEN or a PGN is what the honest-failure
                                           message tells people to do when a request is
                                           too complex to construct, and a capability
                                           mentioned only inside an error message is one
                                           most people never find. */
                                        placeholder="Ask about this position, describe one to set up, or paste a FEN or PGN"
                                        disabled={chatSending || generating || booting || !sessionId}
                                        aria-label="Ask about this position, or describe a position to set up"
                                    />
                                    <button
                                        type="submit"
                                        className="action-btn sandbox-chat-send"
                                        disabled={chatSending || generating || booting || !sessionId || !chatInput.trim()}
                                    >
                                        {chatSending ? 'Asking...' : generating ? 'Building...' : 'Send'}
                                    </button>
                                </form>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
