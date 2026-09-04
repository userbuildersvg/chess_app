import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import type { Square } from 'chess.js';
import { getCustomPieces } from '../pieceThemes';
import { EmptyState } from './EmptyState';
import { useBoardSize } from '../hooks/useBoardSize';
import type { PieceThemeName } from '../pieceThemes';
import { sandboxService } from '../services/sandboxService';
import type {
    SandboxNode,
    SandboxState,
    SandboxScenario,
    NarrationStatus,
    SandboxChatTurn,
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

/** Matches app.py's slider: 20 is strongest, 1 is weakest. */
const DIFFICULTY_MIN = 1;
const DIFFICULTY_MAX = 20;

/**
 * The same five bands the real game's difficulty slider names, so a level
 * means the same thing in both modes. A bare 1-20 is the engine's window
 * position and tells a learner nothing; "12 - Club" does.
 */
const DIFFICULTY_BANDS: { upTo: number; name: string }[] = [
    { upTo: 4, name: 'Beginner' },
    { upTo: 8, name: 'Casual' },
    { upTo: 12, name: 'Club' },
    { upTo: 16, name: 'Strong' },
    { upTo: 20, name: 'Merciless' },
];
const difficultyBand = (level: number): string =>
    (DIFFICULTY_BANDS.find(b => level <= b.upTo) ?? DIFFICULTY_BANDS[DIFFICULTY_BANDS.length - 1]).name;

/** Which of the three right-hand panels is showing. */
type Panel = 'coach' | 'tree' | 'chat';

const PANELS: { id: Panel; label: string; sub: string }[] = [
    { id: 'coach', label: 'Coach', sub: 'What each move accomplishes, and what it rules out' },
    { id: 'tree', label: 'Line', sub: 'Every position explored - click any move to go there' },
    { id: 'chat', label: 'Chat', sub: 'Ask the coach about this position' },
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

export function Sandbox({ onExit }: { onExit: () => void }) {
    // The sandbox frames the board with a prompt bar above and a control
    // stack below, so it needs more vertical room than the game. Measured
    // against the running app rather than guessed: the app header, the
    // scenario bar, the transport row, the merged control row, the status
    // and line rows, and the gaps and padding between them come to just
    // under 392px. At 340 the column ran past the viewport and the page
    // scrolled purely to show a board that was already too tall; at 372 it
    // was still 19px over at 1440x900. This is the largest board that fits
    // 1920, 1440 and 1280 with no scrollbar at all.
    const boardSize = useBoardSize(360);
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

    const [prompt, setPrompt] = useState<string>('');
    const [generating, setGenerating] = useState<boolean>(false);

    // Held separately from `state` on purpose. Only POST /scenario returns a
    // `scenario` block; every other endpoint returns the same body without
    // it. Reading it off `state` meant the description - and more importantly
    // the honest notes ("closest I could get", "verified winning for white") -
    // vanished the moment the first move was played.
    const [scenario, setScenario] = useState<SandboxScenario | null>(null);

    const [autoPlay, setAutoPlay] = useState<boolean>(false);
    // Read inside the auto-play loop, which outlives the render that started
    // it - a state value captured in the closure would be stale forever.
    const autoPlayRef = useRef<boolean>(false);

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

    const [panel, setPanel] = useState<Panel>('coach');

    // --- coach chat -------------------------------------------------------
    // Its own transcript, its own endpoint, its own model chain. Nothing here
    // touches the real game's chat: the two conversations are about different
    // positions and must never appear in each other's history.
    const [chatMessages, setChatMessages] = useState<SandboxChatTurn[]>([]);
    const [chatInput, setChatInput] = useState<string>('');
    const [chatSending, setChatSending] = useState<boolean>(false);
    const [chatError, setChatError] = useState<string | null>(null);
    const chatLogRef = useRef<HTMLDivElement | null>(null);

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
    const [difficultyDraft, setDifficultyDraft] = useState<number | null>(null);

    const [pieceTheme] = useState<PieceThemeName>('stencil');
    const customPieces = useMemo(() => getCustomPieces(pieceTheme), [pieceTheme]);

    const sessionId = state?.session_id ?? null;

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
            try {
                const next = await sandboxService.createSession(12);
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
        return () => {
            autoPlayRef.current = false;
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

    // ----- board / position --------------------------------------------------

    const board = useMemo(() => (state ? new Chess(state.fen) : null), [state]);
    const isGameOver = board?.isGameOver() ?? false;

    /** Nodes from root to current that actually carry a move, oldest first. */
    const line: SandboxNode[] = useMemo(() => {
        if (!state) {
            return [];
        }
        return state.tree.line
            .map(id => state.tree.nodes[id])
            .filter((node): node is SandboxNode => Boolean(node) && node.move !== null);
    }, [state]);

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
     * Promotions auto-queen, matching how the real game handles them in
     * ChessBoard.tsx - `legal_moves` spells them out as e7e8q/e7e8r/..., so
     * the bare from+to is simply absent and has to be retried with a 'q'.
     */
    const uciFor = useCallback((from: string, to: string): string | null => {
        const moves = state?.legal_moves ?? [];
        const plain = `${from}${to}`;
        if (moves.includes(plain)) {
            return plain;
        }
        const queening = `${plain}q`;
        return moves.includes(queening) ? queening : null;
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

    const handleAiMove = useCallback(async () => {
        setError(null);
        setBusy(true);
        await runAiMove();
        setBusy(false);
    }, [runAiMove]);

    // Auto-play walks the AI through both sides. It re-reads the ref each
    // pass so the Pause button takes effect on the next half-move rather
    // than after the whole line.
    const startAutoPlay = useCallback(async () => {
        setError(null);
        setPausing(false);
        autoPlayRef.current = true;
        setAutoPlay(true);
        setBusy(true);
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
        autoPlayRef.current = false;
        setAutoPlay(false);
        setBusy(false);
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
            absorb(await sandboxService.playMove(sessionId, uci, narrateMine));
        } catch (err) {
            setError(err instanceof Error ? err.message : 'That move was not accepted.');
        } finally {
            setBusy(false);
        }
    }, [sessionId, absorb, stopAutoPlay, clearSelection, narrateMine]);

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
                void playUserMove(uci);
                return;
            }
        }
        // Selecting a piece with no legal move would light up an empty set
        // of hints, which reads as a broken board rather than as a pinned
        // piece; leave the previous selection cleared instead.
        setSelectedSquare(legalTargets.has(square) ? square : null);
    }, [interactive, selectedSquare, clearSelection, uciFor, playUserMove, legalTargets]);

    const onPieceDrop = useCallback((from: Square, to: Square): boolean => {
        if (!interactive) {
            return false;
        }
        const uci = uciFor(from, to);
        if (!uci) {
            return false;
        }
        void playUserMove(uci);
        return true;
    }, [interactive, uciFor, playUserMove]);

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
    }, [interactive, selectedSquare, legalTargets, occupied]);

    const handleGenerate = useCallback(async () => {
        const text = prompt.trim();
        if (!text) {
            return;
        }
        stopAutoPlay();
        setError(null);
        setGenerating(true);
        const previous = sessionId;
        try {
            const next = await sandboxService.createScenario(text);
            // A scenario opens its own session, so the old one is now
            // orphaned - drop it rather than leaving it for the TTL sweep.
            if (previous && previous !== next.session_id) {
                void sandboxService.deleteSession(previous).catch(() => {});
            }
            setNarrations({});
            // A new scenario is a new subject. The server side clears itself -
            // /scenario opens a fresh session, whose transcript starts empty -
            // so this is the local copy catching up, not a second policy.
            setChatMessages([]);
            setChatInput('');
            setChatError(null);
            setDifficultyDraft(null);
            clearSelection();
            // A scenario names the side it was built for ("as white"), and
            // that side is whoever is to move in the position it produced.
            setOrientation(next.turn);
            setScenario(next.scenario ?? null);
            absorb(next);
            setPrompt('');
        } catch (err) {
            // Scenario failures are deliberately honest - an opening the
            // book doesn't have, or material that can only ever draw, comes
            // back as a 400 that says which. Show it as written.
            setError(err instanceof Error ? err.message : 'Could not build that scenario.');
        } finally {
            setGenerating(false);
        }
    }, [prompt, sessionId, absorb, stopAutoPlay, clearSelection]);

    // ----- difficulty --------------------------------------------------------

    const sessionDifficulty = state?.difficulty ?? 12;
    const difficultyValue = difficultyDraft ?? sessionDifficulty;
    const difficultyDirty = difficultyValue !== sessionDifficulty;

    /**
     * Restart the line, optionally at a new strength.
     *
     * `POST /reset` keeps this session's own starting position, so a
     * generated rook endgame restarts as that endgame rather than as move
     * one of a normal game. The tree is dropped, which is exactly what
     * "restart" should mean and why changing difficulty is a deliberate
     * second click rather than a side effect of moving the dropdown.
     */
    const handleRestart = useCallback(async () => {
        if (!sessionId) {
            return;
        }
        stopAutoPlay();
        setError(null);
        setBusy(true);
        try {
            const next = await sandboxService.reset(
                sessionId,
                difficultyDirty ? difficultyValue : undefined,
            );
            setNarrations({});
            // The chat is intentionally NOT cleared here. Restart line returns
            // to this session's own starting position; the conversation is
            // about that position and is still relevant to it.
            setDifficultyDraft(null);
            clearSelection();
            setOrientation(next.turn);
            absorb(next);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Could not restart the line.');
        } finally {
            setBusy(false);
        }
    }, [sessionId, difficultyDirty, difficultyValue, absorb, stopAutoPlay, clearSelection]);

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

    const sendChat = useCallback(async () => {
        const message = chatInput.trim();
        if (!message || !sessionId || chatSending) {
            return;
        }
        // Show the question immediately and clear the field. The request can
        // take a few seconds, and leaving the text sitting in the input makes
        // it look like nothing was sent.
        setChatMessages(prev => [...prev, { role: 'user', text: message }]);
        setChatInput('');
        setChatError(null);
        setChatSending(true);
        try {
            const result = await sandboxService.chat(sessionId, message);
            // Trust the server's transcript over the local one - it is the
            // copy Gemini will be replayed on the next question.
            setChatMessages(result.history);
        } catch (err) {
            // Put the question back in the box so it can be retried without
            // being retyped, and drop the optimistic turn: it never landed.
            setChatMessages(prev => prev.slice(0, -1));
            setChatInput(message);
            setChatError(err instanceof Error ? err.message : 'The coach could not answer.');
        } finally {
            setChatSending(false);
        }
    }, [chatInput, sessionId, chatSending]);

    // Keep the newest turn in view. Runs on the pending state too, so the
    // "Thinking..." line is what you are left looking at.
    useEffect(() => {
        const log = chatLogRef.current;
        if (log) {
            log.scrollTop = log.scrollHeight;
        }
    }, [chatMessages, chatSending]);

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
            <div className="sandbox-topbar">
                {/* No eyebrow above this heading. It used to read "Learner
                    Mode", which the header's own Play/Learn switch already
                    says - and says at the moment you choose it, rather than
                    one line below the fact. The line under the title now
                    carries the position's own state instead of a label. */}
                <div className="sandbox-identity">
                    <h2 className="sandbox-title">{state?.title ?? 'Learner Mode'}</h2>
                    <span className="sandbox-subtitle">
                        {booting
                            ? 'Opening a board'
                            : isGameOver
                                ? 'This line is finished'
                                : state
                                    ? `${state.turn === 'white' ? 'White' : 'Black'} to move`
                                    : 'No board yet'}
                    </span>
                </div>

                {/* Decision 3: scenario setup is natural language only. There
                    are deliberately no preset buttons here. */}
                <div className="sandbox-prompt-row">
                    <input
                        className="sandbox-prompt-input"
                        value={prompt}
                        onChange={event => setPrompt(event.target.value)}
                        onKeyDown={event => {
                            if (event.key === 'Enter' && !generating) {
                                void handleGenerate();
                            }
                        }}
                        // Leads with the example, because the example is the
                        // half that teaches what this field accepts - and at
                        // 390px an input clips its placeholder, so whatever
                        // comes first is the only part some people ever read.
                        placeholder="e.g. a hard rook endgame as white"
                        disabled={generating}
                        aria-label="Describe the position you want to study"
                    />
                    <button
                        className="action-btn sandbox-generate-btn"
                        onClick={() => void handleGenerate()}
                        disabled={generating || !prompt.trim()}
                    >
                        {generating ? 'Building...' : 'Build it'}
                    </button>
                </div>

                <button className="action-btn sandbox-exit-btn" onClick={onExit}>
                    Back to game
                </button>
            </div>

            {scenario && (
                <div className="sandbox-scenario-strip fade-slide-in">
                    <p className="sandbox-scenario-desc">{scenario.description}</p>
                    {scenario.notes && <p className="sandbox-scenario-notes">{scenario.notes}</p>}
                </div>
            )}

            {error && <div className="sandbox-error fade-slide-in">{error}</div>}

            <div className="sandbox-body">
                <div className="sandbox-board-column">
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
                                    onSquareClick={onSquareClick}
                                    customSquareStyles={squareStyles}
                                    animationDuration={300}
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
                    </div>

                    <div className="sandbox-controls">
                        <button
                            className="action-btn ai-move-btn"
                            onClick={() => void handleAiMove()}
                            disabled={busy || booting || isGameOver || !state}
                        >
                            AI move
                        </button>
                        {autoPlay ? (
                            <button className="action-btn pause-btn" onClick={stopAutoPlay}>
                                Pause
                            </button>
                        ) : (
                            <button
                                className="action-btn sandbox-play-btn"
                                onClick={() => void startAutoPlay()}
                                disabled={busy || booting || isGameOver || !state}
                            >
                                Play line
                            </button>
                        )}
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
                            disabled={busy || booting}
                            title="Forward one half-move (Right arrow)"
                        >
                            Forward
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

                    <div className="sandbox-takeover-row">
                        <button
                            type="button"
                            className={`sandbox-toggle ${takeover ? 'is-on' : ''}`}
                            onClick={() => {
                                setTakeover(!takeover);
                                clearSelection();
                            }}
                            disabled={booting || !state}
                            aria-pressed={takeover}
                        >
                            {takeover ? 'Playing' : 'Take over'}
                        </button>
                        <label className="sandbox-check">
                            <input
                                type="checkbox"
                                checked={narrateMine}
                                onChange={event => setNarrateMine(event.target.checked)}
                            />
                            {/* Off by default server-side, and left off here for
                                the same reason: narrating every move a student
                                tries while exploring doubles the load on the one
                                shared API key for commentary nobody asked for.

                                Shortened from "Ask the coach about my moves":
                                the row is pinned to the board's width, and the
                                longer label was the one thing that pushed it
                                onto a second line. */}
                            Coach my moves
                        </label>

                        {/* Difficulty and Restart used to sit on their own row
                            below. They belong to the same group - how the
                            demonstration is being driven - and splitting them
                            across two lines cost column height for nothing.
                            The row wraps, so nothing is cramped when it can't
                            fit. */}
                        <span className="sandbox-row-divider" aria-hidden="true" />

                        <label className="sandbox-difficulty">
                            <span>Difficulty</span>
                            <select
                                value={difficultyValue}
                                onChange={event => setDifficultyDraft(Number(event.target.value))}
                                disabled={busy || booting || !state}
                            >
                                {Array.from(
                                    { length: DIFFICULTY_MAX - DIFFICULTY_MIN + 1 },
                                    (_, i) => DIFFICULTY_MIN + i,
                                ).map(value => (
                                    <option key={value} value={value}>
                                        {value} - {difficultyBand(value)}
                                    </option>
                                ))}
                            </select>
                        </label>
                        <button
                            type="button"
                            className={`sandbox-toggle ${difficultyDirty ? 'is-on' : ''}`}
                            onClick={() => void handleRestart()}
                            disabled={busy || booting || !state}
                        >
                            {difficultyDirty ? `Restart at ${difficultyValue} - ${difficultyBand(difficultyValue)}` : 'Restart line'}
                        </button>
                    </div>

                    {takeover && (
                        <p className="sandbox-hint">
                            Your move branches the line permanently - the AI's own continuation
                            stays in the tree, under <strong>Line</strong>.
                        </p>
                    )}

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
                        {panel === 'coach' && (
                            <>
                                {line.length === 0 && (
                                    <EmptyState title="No commentary yet">
                                        Play a line and the coach will talk you
                                        through it, move by move.
                                    </EmptyState>
                                )}
                                {line.map((node, index) => {
                                    const entry = narrations[node.id] ?? {
                                        status: node.narration_status,
                                        narration: node.narration,
                                    };
                                    return (
                                        <article key={node.id} className="sandbox-ply fade-slide-in">
                                            <header className="sandbox-ply-head">
                                                <span className="sandbox-ply-num">{index + 1}</span>
                                                <span className="sandbox-ply-san">{node.san}</span>
                                                <span className={`sandbox-ply-source sandbox-ply-${node.source}`}>
                                                    {node.source === 'ai' ? 'AI' : 'You'}
                                                </span>
                                            </header>
                                            {/* The player's own reasoning. Distinct from the coach below. */}
                                            {node.explanation && (
                                                <p className="sandbox-ply-explanation">{node.explanation}</p>
                                            )}
                                            {entry.status === 'pending' && (
                                                <p className="sandbox-ply-pending">Coach is thinking...</p>
                                            )}
                                            {entry.status === 'failed' && (
                                                <p className="sandbox-ply-failed">
                                                    The coach did not get back in time on this one.
                                                </p>
                                            )}
                                            {entry.status === 'ready' && entry.narration && (
                                                <p className="sandbox-ply-narration">{entry.narration}</p>
                                            )}
                                        </article>
                                    );
                                })}
                            </>
                        )}

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

                        {panel === 'chat' && (
                            <div className="sandbox-chat">
                                <div className="sandbox-chat-log" ref={chatLogRef}>
                                    {chatMessages.length === 0 && (
                                        <EmptyState title="Ask the coach anything">
                                            Why a move was played, what the plan is,
                                            what you should be looking at. It can see
                                            the position and the engine's ranking of
                                            every legal move here.
                                        </EmptyState>
                                    )}
                                    {chatMessages.map((msg, index) => (
                                        <div
                                            key={index}
                                            className={`sandbox-chat-msg sandbox-chat-${msg.role}`}
                                        >
                                            {msg.text}
                                        </div>
                                    ))}
                                    {chatSending && (
                                        <div
                                            className="sandbox-chat-msg sandbox-chat-model is-pending"
                                            role="status"
                                            aria-live="polite"
                                        >
                                            Thinking...
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
                                        void sendChat();
                                    }}
                                >
                                    <input
                                        className="sandbox-chat-input"
                                        value={chatInput}
                                        onChange={event => setChatInput(event.target.value)}
                                        placeholder="e.g. why not Nf3 here?"
                                        disabled={chatSending || booting || !sessionId}
                                        aria-label="Ask the coach about this position"
                                    />
                                    <button
                                        type="submit"
                                        className="action-btn sandbox-chat-send"
                                        disabled={chatSending || booting || !sessionId || !chatInput.trim()}
                                    >
                                        {chatSending ? 'Asking...' : 'Ask'}
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
