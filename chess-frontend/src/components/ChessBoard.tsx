import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Chessboard } from 'react-chessboard';
import { chessService } from '../services/chessService';
import type { GameState, ChessMove, HistoryEntry, LangflowConfig } from '../types/chess';
import type { Square } from 'chess.js';
import { getCustomPieces, getBoardColors, PIECE_THEME_LIST } from '../pieceThemes';
import { EmptyState } from './EmptyState';
import { BoardEndState } from './BoardEndState';
import { EvalBar } from './EvalBar';
import { readBoardStatus } from '../boardState';
// The grade palette and vocabulary, shared with Post-Mortem - see
// ../moveQuality.ts. They were local to this file while the real game was the
// only thing that graded a move.
import { NON_JUDGING_LABELS, qualityColor, gradeSentence } from '../moveQuality';
import type { MoveQuality } from '../moveQuality';
import { difficultyBand, difficultyLabel, DIFFICULTY_LEVELS } from '../difficulty';
import { renderFormattedText } from '../formatText';
import type { PieceThemeName } from '../pieceThemes';
import { apiFetch } from '../services/http';
import { readLocal, writeLocal } from '../services/preferences';
import { PromotionPicker } from './PromotionPicker';
import { isPromotionMove, moverColor } from './promotion';
import type { PendingPromotion, PromotionPiece } from './promotion';
import { markMoveTiming, endMoveTiming } from '../moveTiming';
import { useBoardSizing } from '../hooks/useBoardScale';
import { BoardSizeControl } from './BoardSizeControl';

/**
 * What the coach's state says while it is working.
 *
 * "AI is thinking..." was accurate and unhelpful: it said nothing about what
 * was being thought about, which made a second of waiting feel like a stall.
 * This names the thing that takes the time - the game so far is read on every
 * move, and that is the feature, not the overhead.
 */
interface ChessBoardProps {
    onGameStateChange?: (gameState: GameState) => void;
}
type PositionEval = {
    score: number | null;
    mate_in: number | null;
};
type LearningSummary = {
    opponent: {
        games_played: number;
        wins: number;
        losses: number;
        draws: number;
        blunder_rate: number | null;
        top_openings: Array<{ moves: string }>;
    };
    ai_self: {
        total_ai_moves: number;
        gemini_moves: number;
        gemini_win_rate: number | null;
    };
};
// Maps a position eval to a 0-100 "how much of the bar is White's" fill
// percentage. Centipawns are compressed into +/-1000 (10 pawns) so a single
// blunder doesn't immediately max out the bar - beyond that, one side is
// winning so decisively the exact number stops mattering visually.
const evalToWhitePercent = (evalData: PositionEval): number => {
    if (evalData.mate_in !== null) {
        return evalData.mate_in > 0 ? 100 : 0;
    }
    const cp = evalData.score ?? 0;
    const clamped = Math.max(-1000, Math.min(1000, cp));
    return 50 + (clamped / 1000) * 50;
};
// Formats a position eval the way Chessly/Chess.com do: "+2.1", "-4.6",
// "M3" (White mates in 3), "-M1" (Black mates in 1).
const formatEval = (evalData: PositionEval): string => {
    if (evalData.mate_in !== null) {
        return evalData.mate_in > 0 ? `M${evalData.mate_in}` : `-M${Math.abs(evalData.mate_in)}`;
    }
    if (evalData.score === null) {
        return '0.0';
    }
    const pawns = evalData.score / 100;
    const sign = pawns > 0 ? '+' : '';
    return `${sign}${pawns.toFixed(1)}`;
};
// A single half-move as stored in the backend's game_history: 'explanation'
// is only ever set on AI moves, and only when Gemini provided one.
// Per-color accuracy and grade tallies for the Review panel, computed
// server-side from the same history the badges come from.
type SideAccuracy = {
    accuracy: number | null;
    counts: Record<string, number>;
    graded: number;
};
type AccuracySummary = {
    white: SideAccuracy;
    black: SideAccuracy;
    player_color: 'white' | 'black';
    regrade: { running: boolean; done: number; total: number };
};
// Order grades run best -> worst, for the Review panel's breakdown.
const QUALITY_ORDER = [
    'brilliant', 'great', 'best', 'excellent', 'good', 'book',
    'inaccuracy', 'mistake', 'miss', 'blunder', 'forced'
];
// Grades that carry no verdict about the player's choice, so they get no
// badge on the board or in the move list: a forced move had no alternative,
// and annotating it just adds clutter to every recapture. Still counted in
// the Review breakdown, where the tally is informative rather than noise.
const UNBADGED_LABELS = new Set(['forced']);
// Accuracy and grade tallies are derived here rather than read from the
// server. They are a pure function of the move history the badges already
// come from, so computing them locally keeps the panel exactly in step with
// the board - the previous version only refreshed these numbers inside
// refreshEval(), which stops being called once every move has a grade, so
// the panel froze mid-game and came back empty after a page refresh.
const summarizeSide = (entries: HistoryEntry[]): SideAccuracy => {
    const counts: Record<string, number> = {};
    const scores: number[] = [];
    entries.forEach(entry => {
        const quality = entry?.quality;
        if (!quality) return;
        counts[quality.label] = (counts[quality.label] ?? 0) + 1;
        if (NON_JUDGING_LABELS.has(quality.label)) return;
        if (typeof quality.accuracy === 'number') scores.push(quality.accuracy);
    });
    const accuracy = scores.length
        ? Math.round((scores.reduce((a, b) => a + b, 0) / scores.length) * 10) / 10
        : null;
    return { accuracy, counts, graded: scores.length };
};
type MovePair = {
    moveNumber: number;
    white: string;
    black: string;
    isLastWhite: boolean;
    isLastBlack: boolean;
    blackExplanation: string | null;
    whiteQuality: MoveQuality | null;
    blackQuality: MoveQuality | null;
};
// Captured material, derived from the FEN we already poll rather than from
// any new endpoint: count what each side still has on the board and diff it
// against a full starting set. Returns the pieces the given color has taken
// FROM the opponent, heaviest first, so the strip reads like a scoreboard.
const FULL_SET: Record<string, number> = { q: 1, r: 2, b: 2, n: 2, p: 8 };
const PIECE_VALUE: Record<string, number> = { q: 9, r: 5, b: 3, n: 3, p: 1 };
const CAPTURED_GLYPH: Record<string, string> = {
    q: '\u265B', r: '\u265C', b: '\u265D', n: '\u265E', p: '\u265F',
};
const capturedBy = (fen: string, side: 'white' | 'black'): string[] => {
    const placement = (fen || '').split(' ')[0];
    // The pieces we count are the OPPONENT's survivors: what's missing from
    // their set is what `side` has captured.
    const opponentIsWhite = side === 'black';
    const alive: Record<string, number> = { q: 0, r: 0, b: 0, n: 0, p: 0 };
    for (const ch of placement) {
        if (ch === '/' || (ch >= '1' && ch <= '8')) continue;
        const isWhitePiece = ch === ch.toUpperCase();
        if (isWhitePiece !== opponentIsWhite) continue;
        const key = ch.toLowerCase();
        if (key in alive) alive[key] += 1;
    }
    const taken: string[] = [];
    for (const key of Object.keys(FULL_SET)) {
        // Promotions can push a count above the starting set; clamp at 0 so a
        // promoted queen never renders as a negative capture.
        const missing = Math.max(0, FULL_SET[key] - alive[key]);
        for (let i = 0; i < missing; i += 1) taken.push(key);
    }
    return taken.sort((a, b) => PIECE_VALUE[b] - PIECE_VALUE[a]);
};
// Net material edge in pawns, shown as +3 next to the captures. Nothing is
// shown when the material is level - a "+0" is noise.
const materialEdge = (fen: string, side: 'white' | 'black'): number => {
    const mine = capturedBy(fen, side).reduce((n, k) => n + PIECE_VALUE[k], 0);
    const theirs = capturedBy(fen, side === 'white' ? 'black' : 'white')
        .reduce((n, k) => n + PIECE_VALUE[k], 0);
    return mine - theirs;
};
// Groups the flat half-move history into numbered White/Black rows for the
// move history panel, the same way a PGN move list reads.
const buildMovePairs = (history: HistoryEntry[]): MovePair[] => {
    const pairs: MovePair[] = [];
    const lastIndex = history.length - 1;
    for (let i = 0; i < history.length; i += 2) {
        const whiteEntry = history[i];
        const blackEntry = history[i + 1];
        pairs.push({
            moveNumber: i / 2 + 1,
            white: whiteEntry?.san ?? '',
            black: blackEntry?.san ?? '',
            isLastWhite: i === lastIndex,
            isLastBlack: i + 1 === lastIndex,
            blackExplanation: blackEntry?.explanation ?? null,
            whiteQuality: whiteEntry?.quality ?? null,
            blackQuality: blackEntry?.quality ?? null
        });
    }
    return pairs;
};
// Rank and file labels.
//
// react-chessboard's default paints each label in the OTHER square's colour,
// which is only ever as legible as the gap between the two square colours.
// This board's pair measures 3.05:1 in dark and 2.35:1 in light, so the
// coordinates were the only text in the app below AA - on every label of both
// boards, in both themes.
//
// A label sits ON a square, so its contrast is a property of whichever square
// it lands on and one colour cannot serve both. This is the printed-diagram
// answer: dark ink carried on its own light halo, which reads the same way on
// a light square and a dark one. The halo is what a contrast checker cannot
// see - measured against the bare square the ink is still 3.4:1 on the dark
// square - so the thing being relied on here is the plate, not the pair.
const BOARD_NOTATION_STYLE: Record<string, string | number> = {
    color: 'var(--board-notation)',
    fontFamily: 'var(--font-mono)',
    fontWeight: 700,
    textShadow:
        '0 0 2px var(--board-notation-halo), 0 0 2px var(--board-notation-halo),'
        + ' 0 0 3px var(--board-notation-halo), 0 0 4px var(--board-notation-halo)',
};
type GameMode = 'human_vs_ai' | 'ai_vs_ai';
// The badge used to print the internal status key straight out ("AI: idle",
// "AI: connected"), which is a field name and a variable rather than anything
// a player is being told. Same four states, said in the app's own voice.
const AI_STATUS_TEXT: Record<string, string> = {
    idle: 'Coach ready',
    thinking: 'Coach thinking',
    connected: 'Coach ready',
    error: 'Coach offline',
};
// Rail Canvas layout: Moves is always-visible next to the board (see
// .moves-panel in ChessBoard.css), the same treatment as the eval bar.
// Chat, Review, Progress, Board and Actions switch via the icon rail.
//
// There used to be a Coach tab beside Chat. It showed the explanation Gemini
// gave for its latest move - and only the latest, replaced on every move, on
// a different tab from the box you would ask "why?" in. The explanation is a
// message in the conversation now (PlayerSession.note_coach_turn on the
// server), so it arrives where the question about it gets asked, stays put
// when the next one arrives, and is in the history the model is replayed.
type RailSectionId = 'theme' | 'learning' | 'chat' | 'review' | 'actions';
const RAIL_SECTIONS: { id: RailSectionId; label: string }[] = [
    { id: 'chat', label: 'Chat' },
    // Move grading gets its own rail slot rather than being wedged into an
    // existing panel: it needs room for two accuracy figures, a grade
    // breakdown and a re-grade control, and the rail is exactly the
    // established place for a panel that size. Nothing else has to move.
    { id: 'review', label: 'Review' },
    { id: 'learning', label: 'Progress' },
    { id: 'theme', label: 'Board' },
    // The utilities that used to sit under the board and are reached for
    // once a game, if that: spectating an AI-vs-AI game, the coordinate
    // labels, and clearing the conversation. What stays under the board is
    // what is touched while playing.
    { id: 'actions', label: 'Actions' },
];
// One turn of the Play conversation as the panel draws it. `move` is set on
// the coach's explanation of its own move - the server tags those turns so
// the bubble can carry the move it is about.
type ChatMessage = { role: 'user' | 'ai'; text: string; move?: string };
const toChatMessages = (history: unknown): ChatMessage[] | null => {
    if (!Array.isArray(history)) {
        return null;
    }
    return history.map((turn: { role: string; text: string; move?: string }) => ({
        role: turn.role === 'model' ? 'ai' : 'user',
        text: turn.text,
        ...(turn.move ? { move: turn.move } : {}),
    }));
};
export const ChessBoard: React.FC<ChessBoardProps> = ({ onGameStateChange }) => {
    const [gameState, setGameState] = useState<GameState>(chessService.getGameState());
    const [langflowConfig, setLangflowConfig] = useState<LangflowConfig>({
        flowId: '8f2ac28a-4b1b-4bb0-8703-70e58cb00def',
        isConnected: false,
        status: 'idle'
    });
    const [selectedSquare, setSelectedSquare] = useState<Square | null>(null);
    // The last thing that went wrong, for the strip under the board.
    //
    // Play used to swallow these entirely: `makePlayerMove` ended
    // `if (result.success) { ... } else { }` and `handleReset` ended the same
    // way, so a refused move, a 500 or a dropped connection produced a board
    // that simply did not move and a strip that still read "Coach ready".
    // The user's only evidence that anything had happened was that nothing
    // had. Learn and Review have both always shown their failures
    // (`.sandbox-error`, `pm-error`); this is Play catching up, in the row it
    // already has rather than in a new one.
    const [moveError, setMoveError] = useState<string | null>(null);
    // The square a drag started from. Deliberately separate from
    // selectedSquare: click-to-move and drag-to-move are both live at all
    // times and neither is a mode, so a drag lights its own destinations
    // without destroying a selection the user may still come back to.
    const [dragFrom, setDragFrom] = useState<Square | null>(null);
    // Header, both player strips and the container padding come to roughly
    // 300px of vertical chrome around the board in this mode.
    // The same measured fitter Learn and Review use, rather than the fixed
    // `chrome = 300` this had. That constant was the height of the old layout's
    // furniture, and the layout's furniture is exactly what changed: the board
    // column now carries the two player strips, a status line, the transport
    // and the meta row, and any constant is wrong again the next time a row is
    // added under the board. The fitter measures the page's real overflow
    // instead, so whatever ends up under the board, the correction is still
    // the overflow. Off while stacked - see hooks/useStacked.ts.
    const boardColumnRef = useRef<HTMLDivElement>(null);
    // Preference, ceiling, fit and shrink in one call - the growing and
    // shrinking halves are not symmetrical and the reasoning lives in one
    // place rather than three. See hooks/useBoardScale.
    const { pref: boardSizePref, setPref: setBoardSizePref, boardSize } =
        useBoardSizing(boardColumnRef, 180);
    const [moveCount, setMoveCount] = useState<number>(0);
    const [difficulty, setDifficulty] = useState<number>(20);
    const [boardEval, setBoardEval] = useState<PositionEval>({ score: 0, mate_in: null });
    const [moveHistory, setMoveHistory] = useState<HistoryEntry[]>([]);
    // Which color the human is playing - the AI always plays the other
    // color while gameMode is 'human_vs_ai'. Drives board orientation and
    // which side onSquareClick allows the human to move.
    const [playerColor, setPlayerColorState] = useState<'white' | 'black'>('white');
    // 'ai_vs_ai' disables human input entirely and swaps the action-button
    // row for the auto-play controls (Pause/Resume/Next Move/Exit).
    const [gameMode, setGameMode] = useState<GameMode>('human_vs_ai');
    const [aiVsAiRunning, setAiVsAiRunning] = useState<boolean>(false);
    // The conversation with the coach (see /api/chat). The SERVER owns it:
    // every reply and every status read hands the whole transcript back and
    // this mirrors it, which is what lets the coach's move explanations -
    // appended server-side as the AI moves - appear in order between the
    // questions without the browser having to guess where they go. Per-game
    // only, cleared server-side whenever a new game starts (reset /
    // colour-switch / AI-vs-AI start).
    const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
    const [chatInput, setChatInput] = useState<string>('');
    // The question in flight, drawn at the foot of the log until the server
    // answers and its copy of the transcript (which then includes it) wins.
    const [chatPending, setChatPending] = useState<string | null>(null);
    const chatSending = chatPending !== null;
    // A failure to reach the coach - for a question or for a move - goes
    // under the log rather than into it, so a transcript that is otherwise
    // the server's does not carry a bubble the server never saw.
    const [chatError, setChatError] = useState<string | null>(null);
    // Live cross-game learning summary (see learning_service.py) - polled on
    // its own timer rather than wired into every move-completion path, so
    // the panel stays current without touching the already-intricate
    // polling logic used for AI moves and AI-vs-AI auto-play.
    const [learningSummary, setLearningSummary] = useState<LearningSummary | null>(null);
    // Which rail section the canvas is showing, plus a small unread dot for
    // the others when they get new content while unfocused.
    //
    // Persisted to localStorage: a page refresh used to always drop you back
    // on the first tab, which is a particular nuisance mid-game when you were
    // watching Review. Restores whatever was open, falling back to Chat -
    // which is where the coach speaks - if nothing valid is stored. A stored
    // 'analysis' (the old Coach tab) fails the check and lands on Chat too.
    const [activeSection, setActiveSection] = useState<RailSectionId>(() => {
        try {
            const stored = readLocal('chess-active-section');
            if (stored && RAIL_SECTIONS.some(section => section.id === stored)) {
                return stored as RailSectionId;
            }
        } catch {
            // localStorage unavailable - fall through to the default
        }
        return 'chat';
    });
    useEffect(() => {
        try {
            writeLocal('chess-active-section', activeSection);
        } catch {
            // localStorage unavailable - the tab just won't persist
        }
    }, [activeSection]);
    const [unreadSections, setUnreadSections] = useState<Record<RailSectionId, boolean>>({
        learning: false, chat: false, theme: false, review: false, actions: false
    });
    // Bumped whenever learningSummary actually changes content. Used as a
    // React `key` on that section's canvas wrapper below so a fresh value
    // forces a remount - replaying the fade-slide-in CSS animation even when
    // the user is already looking at that section, instead of the new text
    // just popping into place.
    const [learningUpdateKey, setLearningUpdateKey] = useState(0);
    const prevLearningJsonRef = useRef<string>('');
    const prevChatLengthRef = useRef<number>(0);
    // Which piece/board visual theme is active - defaults to 'stencil'.
    // react-chessboard's own built-in piece rendering ('classic') has been
    // dropped entirely from pieceThemes.tsx, so every option in the picker
    // is one of our own zero-licensing-obligation custom sets.
    //
    // Persisted to localStorage so it survives a page refresh - initialized
    // lazily from whatever was last saved (falling back to 'stencil' if
    // nothing's saved, or if a stale value from a since-removed theme is
    // sitting there), and written back out below whenever it changes.
    const [pieceTheme, setPieceTheme] = useState<PieceThemeName>(() => {
        try {
            const stored = readLocal('chess-piece-theme');
            if (stored && PIECE_THEME_LIST.some(theme => theme.id === stored)) {
                return stored as PieceThemeName;
            }
        } catch {
            // localStorage unavailable (private browsing, etc.) - fall through to default
        }
        return 'stencil';
    });
    useEffect(() => {
        try {
            writeLocal('chess-piece-theme', pieceTheme);
        } catch {
            // localStorage unavailable - theme just won't persist this session
        }
    }, [pieceTheme]);
    // Whether to grade moves and show the badges. Persisted locally for an
    // instant, correct first paint (no flash of badges before the server
    // answers), then reconciled with the server on mount - the server is
    // the real owner of this setting, since switching it off has to stop
    // the per-move Stockfish search, not just hide the UI.
    // Two display switches for the left column. Both are purely local -
    // they change what this screen shows, never what the engine does - so
    // they persist in localStorage rather than round-tripping to the server.
    // Engine numbers default OFF: a centipawn score is noise to someone who
    // is still learning what a fork is, and the coach text says the same
    // thing in words.
    const [showEngineNumbers, setShowEngineNumbers] = useState<boolean>(() => {
        try {
            return readLocal('chess-engine-numbers') === 'true';
        } catch {
            return false;
        }
    });
    useEffect(() => {
        try {
            writeLocal('chess-engine-numbers', String(showEngineNumbers));
        } catch {
            /* private mode - the toggle still works for this session */
        }
    }, [showEngineNumbers]);
    const [showCoordinates, setShowCoordinates] = useState<boolean>(() => {
        try {
            return readLocal('chess-coordinates') !== 'false';
        } catch {
            return true;
        }
    });
    useEffect(() => {
        try {
            writeLocal('chess-coordinates', String(showCoordinates));
        } catch {
            /* as above */
        }
    }, [showCoordinates]);
    const [showMoveQuality, setShowMoveQuality] = useState<boolean>(() => {
        try {
            const stored = readLocal('chess-move-quality');
            if (stored !== null) return stored === 'true';
        } catch {
            // localStorage unavailable - fall through to the default
        }
        return true;
    });
    useEffect(() => {
        try {
            writeLocal('chess-move-quality', String(showMoveQuality));
        } catch {
            // localStorage unavailable - setting just won't persist
        }
    }, [showMoveQuality]);
    const [accuracySummary, setAccuracySummary] = useState<AccuracySummary | null>(null);
    const [regrading, setRegrading] = useState<boolean>(false);
    const moveHistoryListRef = useRef<HTMLDivElement>(null);
    const aiVsAiPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
    useEffect(() => {
        const initializeGame = async () => {
            try {
                    const response = await apiFetch('/api/status');
                    const data = await response.json();
                    if (data.success && data.status) {
                        chessService.loadPosition(data.status.fen);
                        const newGameState = chessService.getGameState();
                        setGameState(newGameState);
                        setMoveCount(data.status.move_count);
                        if (typeof data.difficulty === 'number') {
                            setDifficulty(data.difficulty);
                        }
                        if (data.eval) {
                            setBoardEval(data.eval);
                        }
                        setMoveHistory(data.history || []);
                        // Reconcile the grading toggle. The locally stored
                        // preference wins over the server's: the server
                        // resets to its default whenever the process
                        // restarts, so trusting it here would silently turn
                        // grading back on for someone who'd switched it off.
                        if (typeof data.move_quality_enabled === 'boolean'
                            && data.move_quality_enabled !== showMoveQuality) {
                            fetch('/api/move-quality', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ enabled: showMoveQuality })
                            }).catch(() => { /* non-fatal - badges just stay as they are */ });
                        }
                        // Restore the conversation on load/refresh - the
                        // backend keeps it (chat_history is only cleared
                        // server-side when a new game actually starts), the
                        // coach's move explanations included.
                        const restored = toChatMessages(data.chat_history);
                        if (restored) {
                            setChatMessages(restored);
                        }
                        if (data.player_color === 'white' || data.player_color === 'black') {
                            setPlayerColorState(data.player_color);
                        }
                        if (data.game_mode === 'ai_vs_ai') {
                            setGameMode('ai_vs_ai');
                            setAiVsAiRunning(!!data.ai_vs_ai_running);
                            if (data.ai_vs_ai_running) {
                                startAiVsAiPolling();
                            }
                        }
                }
            } catch (error) {
                console.error('❌ [ERROR] Error initializing game:', error);
            }
        };
        initializeGame();
        return () => {
            if (aiVsAiPollRef.current) {
                clearInterval(aiVsAiPollRef.current);
            }
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    useEffect(() => {
        onGameStateChange?.(gameState);
    }, [gameState, onGameStateChange]);
    useEffect(() => {
        if (moveHistoryListRef.current) {
            moveHistoryListRef.current.scrollTop = moveHistoryListRef.current.scrollHeight;
        }
    }, [moveHistory]);
    // Learning panel data - fetched once on mount and then re-polled every
    // few seconds, independent of the game's own move-completion polling.
    const fetchLearningSummary = useCallback(async () => {
        try {
            const response = await apiFetch('/api/learning/summary');
            const data = await response.json();
            if (data.success) {
                // Backend responses have historically flattened their payload
                // onto the top-level object rather than nesting it under a
                // "data" key (see data.status/data.eval elsewhere in this
                // file) - checked defensively here in case that's wrong.
                setLearningSummary(data.opponent ? data : (data.data ?? data));
            }
        } catch (error) {
            console.error('❌ [LEARNING] Failed to fetch learning summary:', error);
        }
    }, []);
    useEffect(() => {
        fetchLearningSummary();
        const interval = setInterval(fetchLearningSummary, 5000);
        return () => clearInterval(interval);
    }, [fetchLearningSummary]);
    // Kept in a ref (not just read from `activeSection` state) so the
    // content-change effects below can check "is the user looking at this
    // section right now" without needing activeSection in their own
    // dependency array - which would otherwise re-fire them on every
    // section switch.
    const activeSectionRef = useRef<RailSectionId>('chat');
    useEffect(() => {
        activeSectionRef.current = activeSection;
    }, [activeSection]);
    const setActiveTabAwareUnread = useCallback((section: RailSectionId) => {
        if (activeSectionRef.current !== section) {
            setUnreadSections(prev => ({ ...prev, [section]: true }));
        }
    }, []);
    const handleSectionClick = useCallback((section: RailSectionId) => {
        setActiveSection(section);
        setUnreadSections(prev => ({ ...prev, [section]: false }));
    }, []);
    // learningSummary is re-fetched on a 5s timer whether or not anything
    // actually changed - compare the serialized payload first so polling
    // alone doesn't replay the animation or flag "unread" every 5 seconds
    // for no reason.
    //
    // The FIRST payload is not an update. It is the panel's initial contents
    // arriving, and flagging it unread put a "new content" dot on Progress on
    // every page load, before the user had had the chance to read anything -
    // which is the one thing that badge must never do, because it teaches
    // people to ignore it.
    useEffect(() => {
        if (!learningSummary) {
            return;
        }
        const json = JSON.stringify(learningSummary);
        if (json === prevLearningJsonRef.current) {
            return;
        }
        const isFirstLoad = prevLearningJsonRef.current === '';
        prevLearningJsonRef.current = json;
        if (isFirstLoad) {
            return;
        }
        setLearningUpdateKey(k => k + 1);
        setActiveTabAwareUnread('learning');
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [learningSummary]);
    // Flag Chat unread only when the coach says something - a reply, or the
    // explanation of a move it has just played - not on the user's own
    // outgoing message, which they obviously already saw.
    useEffect(() => {
        if (chatMessages.length > prevChatLengthRef.current) {
            const last = chatMessages[chatMessages.length - 1];
            if (last?.role === 'ai') {
                setActiveTabAwareUnread('chat');
            }
        }
        prevChatLengthRef.current = chatMessages.length;
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [chatMessages]);
    const clearSelection = useCallback(() => {
        setSelectedSquare(null);
        setDragFrom(null);
    }, []);
    const updateGameState = useCallback(() => {
        const newGameState = chessService.getGameState();
        setGameState(newGameState);
        clearSelection();
    }, [clearSelection]);
    // Re-reads the evaluation AND the conversation from /api/status. The two
    // travel together because they change together: every AI move adds a
    // coach turn to the server's transcript, and every path that already
    // re-read the eval after a move is exactly the set that needs the new
    // turn.
    const refreshEval = useCallback(async () => {
        try {
            const response = await apiFetch('/api/status');
            const data = await response.json();
            const history = data.success ? toChatMessages(data.chat_history) : null;
            if (history) {
                setChatMessages(history);
            }
            if (data.success && data.eval) {
                setBoardEval(data.eval);
            }
            if (data.success && data.history) {
                setMoveHistory(data.history);
            }
            if (data.success && data.accuracy) {
                setAccuracySummary(data.accuracy);
                setRegrading(!!data.accuracy.regrade?.running);
            }
        } catch (error) {
            console.error('❌ [EVAL] Failed to refresh evaluation:', error);
        }
    }, []);
    const toggleMoveQuality = useCallback(async () => {
        const next = !showMoveQuality;
        setShowMoveQuality(next);
        try {
            await apiFetch('/api/move-quality', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: next })
            });
            // Turning it back on doesn't retroactively grade moves played
            // while it was off, but grades from before then are still on the
            // server - pull them back so they reappear immediately.
            if (next) {
                refreshEval();
            }
        } catch (error) {
            console.error('❌ [QUALITY] Failed to update move quality setting:', error);
        }
    }, [showMoveQuality, refreshEval]);
    const handleRegrade = useCallback(async () => {
        setRegrading(true);
        try {
            await apiFetch('/api/move-quality/regrade', { method: 'POST' });
        } catch (error) {
            console.error('❌ [QUALITY] Re-grade request failed:', error);
            setRegrading(false);
        }
    }, []);
    // While a re-grade is running, keep pulling status so the progress
    // counter advances and badges fill in as each move is graded.
    useEffect(() => {
        if (!regrading) {
            return;
        }
        const timer = setInterval(() => { refreshEval(); }, 700);
        return () => clearInterval(timer);
    }, [regrading, refreshEval]);
    // Grading runs in the background on the server and takes ~300-500ms, by
    // which point the poll loop that was watching for the move itself has
    // usually already stopped. So top the history up until the latest moves
    // have their grades.
    //
    // The first check is deliberately fast (a grade is typically ready by
    // then, which is what makes the badge feel instant) and the interval
    // stays short. It's bounded, because a grade can legitimately never
    // arrive - Stockfish refuses to analyse some positions - and that must
    // not turn into an endless poll.
    const gradePollRef = useRef<{ len: number; attempts: number }>({ len: -1, attempts: 0 });
    useEffect(() => {
        if (!showMoveQuality || moveHistory.length === 0) {
            return;
        }
        // Only the last two plies can still be awaiting a grade - anything
        // older either has one or never will.
        const awaitingGrade = moveHistory.slice(-2).some(entry => !entry.quality);
        if (!awaitingGrade) {
            return;
        }
        if (gradePollRef.current.len !== moveHistory.length) {
            gradePollRef.current = { len: moveHistory.length, attempts: 0 };
        }
        const attempt = gradePollRef.current.attempts;
        if (attempt >= 14) {
            return;
        }
        const timer = setTimeout(() => {
            gradePollRef.current.attempts += 1;
            refreshEval();
        }, attempt === 0 ? 220 : 350);
        return () => clearTimeout(timer);
    }, [moveHistory, showMoveQuality, refreshEval]);
    /**
     * Play one half-move for the human.
     *
     * Two things changed here in the beta-hardening pass, and both are about
     * how long the board looks frozen after a click.
     *
     * **The pre-flight `/api/status` is gone.** Every move used to spend a
     * whole round trip re-syncing before it was even sent, so the piece did
     * not leave its square until two requests had completed. That check's job
     * - "is it still my turn?" - was already done twice over: `interactive`
     * gates every entry point on it, and the server refuses a move that is not
     * yours with a message this function surfaces. It bought a third check at
     * the cost of doubling the latency of the only interaction in the mode.
     *
     * **The move is shown before it is confirmed.** chess.js plays it locally
     * first, from the position the server last gave us, and only a move it
     * agrees is legal is drawn at all. The server remains the authority: on
     * any refusal the board is put back and then re-read from `/api/status`,
     * and the reason goes on the strip. What the player gets is their piece
     * moving at once and "Thinking..." in the same frame, instead of a second
     * of a board that looks broken - the tester's "the AI takes too long" was
     * about 1s of engine time and about as much again of this.
     */
    const makePlayerMove = useCallback(async (
        from: Square,
        to: Square,
        promotion?: PromotionPiece,
    ) => {
        const move: ChessMove = { from, to, promotion };
        const t0 = performance.now();
        // Where to go back to if the server refuses. Captured before the
        // optimistic move so the revert is exact rather than re-derived.
        const fenBefore = chessService.getGameState().fen;

        // Optimistic render. `shown === false` means chess.js did not accept
        // the move, and the board then waits for the server exactly as it used
        // to - slower, but never showing a move that will not be played.
        const shown = chessService.applyLocalMove(move);
        if (shown) {
            setMoveError(null);
            const local = chessService.getGameState();
            setGameState(local);
            setMoveCount(local.move_count);
            // The coach's state is set in the same frame the piece lands in,
            // not when the server gets round to confirming it. If it turns out
            // not to be the AI's turn, the response corrects this a moment
            // later - and being briefly wrong about that is invisible, while
            // being briefly frozen is the thing that got reported.
            if (local.turn !== playerColor && !local.is_game_over) {
                setLangflowConfig(prev => ({ ...prev, status: 'thinking' }));
            }
        }

        const result = await chessService.makePlayerMove(move);
        markMoveTiming('server answered', t0);
        if (result.success) {
            setMoveError(null);
            updateGameState();
            setMoveCount(result.status?.move_count ?? moveCount + 1);
            refreshEval();
            if (result.model_move) {
                if (result.model_move.ai_scheduled) {
                    setLangflowConfig(prev => ({ ...prev, status: 'thinking' }));
                    startAiMovePolling();
                } else if (result.model_move.manual_ai_required) {
                    setLangflowConfig(prev => ({ ...prev, status: 'idle' }));
                } else if (result.model_move.success) {
                    // The reply came back inline; its explanation is on the
                    // server's transcript, which refreshEval above re-read.
                    setLangflowConfig(prev => ({ ...prev, status: 'connected' }));
                } else {
                    setLangflowConfig(prev => ({ ...prev, status: 'idle' }));
                }
            }
        } else {
            // The move did not happen. Put the board back, then re-read from
            // the server rather than trusting that rollback: if the refusal was
            // "not your turn", the server's position is AHEAD of ours and the
            // local undo would leave the board a move behind reality.
            if (shown) {
                chessService.loadPosition(fenBefore);
                setGameState(chessService.getGameState());
                setLangflowConfig(prev => ({ ...prev, status: 'idle' }));
                updateGameState();
            }
            setMoveError(result.error || 'That move was not accepted.');
        }
        // startAiMovePolling is deliberately NOT in this dependency list. It is
        // declared below and hoisted at call time, which is how this always
        // worked; naming it here is a use-before-declaration error rather than
        // a correctness improvement, because the callback it would capture does
        // not exist yet on this render.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [updateGameState, refreshEval, playerColor, moveCount]);

    // ----- Promotion ---------------------------------------------------------
    //
    // Both ways of moving route a promotion through the picker rather than
    // submitting one. Auto-queening was the only option here, which is right
    // most of the time and wrong in exactly the positions a learner most needs
    // to understand: the knight that forks on promotion, the rook that
    // promotes without stalemating. See PromotionPicker.tsx for why the
    // library's own dialog is not used - it opens on a drag and not on a
    // click, and CLAUDE.md §19 has already decided those are one interaction.
    const [pendingPromotion, setPendingPromotion] = useState<PendingPromotion | null>(null);

    /**
     * True when the move needs a piece chosen and the question has now been
     * asked; false when it is an ordinary move for the caller to play.
     */
    const askPromotion = useCallback((from: Square, to: Square): boolean => {
        if (!isPromotionMove(gameState.fen, from, to)) {
            return false;
        }
        setPendingPromotion({ from, to, color: moverColor(gameState.fen, from) });
        return true;
    }, [gameState.fen]);

    // ----- Legality: one list, both interactions -----------------------------
    //
    // `gameState.legal_moves` is UCI for exactly this position. Every path
    // that changes the board fills it from python-chess (`/api/status`,
    // `/api/move`, `/api/ai-move` all return `legal_moves`), and the local
    // paths fill it from chess.js loaded with that same FEN - the same list
    // by construction, in the same format. Click-to-move and drag-to-move
    // both read this and nothing else, so the two interactions cannot
    // disagree with each other about what is legal, and neither can offer a
    // destination the backend will then refuse.
    //
    // Everything the spec asks for follows from using the real generator
    // rather than a UI-side approximation of one: a pinned piece simply has
    // no entry for the square it cannot move to, a king in check has only
    // the squares that are actually safe, and while you are in check the
    // only moves in the list are the ones that end it.
    const legalTargets = useMemo(() => {
        const map = new Map<string, Set<string>>();
        for (const uci of gameState.legal_moves ?? []) {
            const from = uci.slice(0, 2);
            let targets = map.get(from);
            if (!targets) {
                targets = new Set<string>();
                map.set(from, targets);
            }
            // A promotion is spelled out four times (e7e8q/r/b/n) and they
            // all land on the same square, so the Set collapses them - the
            // hint is "you may go here", and the piece is chosen below.
            targets.add(uci.slice(2, 4));
        }
        return map;
    }, [gameState.legal_moves]);

    /** Occupied squares, for telling a capture hint from a quiet one. */
    const occupied = useMemo(() => {
        const set = new Set<string>();
        const placement = (gameState.fen || '').split(' ')[0];
        let file = 0;
        let rank = 8;
        for (const ch of placement) {
            if (ch === '/') { rank -= 1; file = 0; continue; }
            if (ch >= '1' && ch <= '9') { file += Number(ch); continue; }
            set.add(`${'abcdefgh'[file]}${rank}`);
            file += 1;
        }
        return set;
    }, [gameState.fen]);

    // Check / checkmate / stalemate / draw, read once. The booleans are the
    // server's (see ../boardState.ts); only the checked king's square and
    // the wording are derived here.
    const boardStatus = useMemo(
        () => readBoardStatus(gameState.fen, gameState),
        [gameState],
    );

    // The single gate on human input. Everything that must stop the board
    // stops it here and only here - the game being over by any of the four
    // endings, the AI thinking, AI-vs-AI, and it not being your turn - so
    // click, drag and draggability are locked by one value and no path can
    // be left open by accident.
    const interactive = gameMode === 'human_vs_ai'
        && !boardStatus.gameOver
        && !gameState.is_game_over
        && gameState.turn === playerColor
        && langflowConfig.status !== 'thinking';

    const onSquareClick = useCallback((square: Square) => {
        // Any fresh attempt clears the last complaint - it is about the move
        // that failed, not a standing condition.
        if (moveError) setMoveError(null);
        if (!interactive) {
            // A finished or waiting board must not light up. Hints under a
            // checkmate overlay would say "you may move", which is the one
            // thing the overlay is there to deny.
            setSelectedSquare(null);
            return;
        }
        if (selectedSquare === square) {
            setSelectedSquare(null);
            return;
        }
        if (selectedSquare && legalTargets.get(selectedSquare)?.has(square)) {
            setSelectedSquare(null);
            // A promotion asks first. `askPromotion` returns true when it has
            // taken the move over, so the click path and the drop path below
            // both read as "play it, unless it is a promotion".
            if (!askPromotion(selectedSquare, square)) {
                void makePlayerMove(selectedSquare, square);
            }
            return;
        }
        // A piece with no legal move selects nothing: lighting an empty set
        // of hints reads as a broken board rather than as a pinned piece.
        setSelectedSquare(legalTargets.has(square) ? square : null);
    }, [interactive, selectedSquare, legalTargets, makePlayerMove, moveError, askPromotion]);

    // ----- Drag, as a second way to say the same thing -----------------------
    //
    // react-chessboard's drag is pointer-based, so this is one implementation
    // for mouse, touch and pen. It is added ALONGSIDE the click handler above,
    // not instead of it: both read `legalTargets`, both call `makePlayerMove`,
    // and there is no mode, toggle or preference separating them.

    /** Only a piece that actually has somewhere to go can be picked up. */
    const isDraggablePiece = useCallback(
        ({ sourceSquare }: { sourceSquare: Square }) => interactive && legalTargets.has(sourceSquare),
        [interactive, legalTargets],
    );

    // Fired the moment the pointer lifts a piece, which is what lets the
    // legal destinations light up DURING the drag rather than after it. The
    // alternative - drag anywhere, judge on release - is exactly the
    // interaction this is meant not to be.
    const onPieceDragBegin = useCallback((_piece: string, sourceSquare: Square) => {
        if (moveError) setMoveError(null);
        if (!interactive || !legalTargets.has(sourceSquare)) {
            return;
        }
        setDragFrom(sourceSquare);
        // A drag owns the hints for its duration and leaves nothing behind:
        // any earlier click-selection is dropped here, so a cancelled drag
        // ends with a quiet board however it was cancelled - released on an
        // illegal square, or dragged back onto the square it started from.
        setSelectedSquare(null);
    }, [interactive, legalTargets, moveError]);

    const onPieceDragEnd = useCallback(() => {
        setDragFrom(null);
    }, []);

    // Returning false snaps the piece home, and that is the clean revert for
    // every illegal release there is: a square the piece cannot reach, a
    // pinned piece's "obvious" square, a king stepping into check, any move
    // that leaves an existing check standing, and the origin square itself
    // (which is never one of its own destinations). No illegal move is ever
    // played and then undone - it is simply not in the list that was offered.
    const onPieceDrop = useCallback((from: Square, to: Square): boolean => {
        setDragFrom(null);
        setSelectedSquare(null);
        if (!interactive || !legalTargets.get(from)?.has(to)) {
            return false;
        }
        // A dropped promotion opens the picker and returns false, which snaps
        // the pawn home while the question is on screen. That is the honest
        // shape: the move has not been played yet, so the board should not
        // show it played. The pawn slides to the last rank when the piece is
        // chosen, which is also the only point at which the move is real.
        if (askPromotion(from, to)) {
            return false;
        }
        void makePlayerMove(from, to);
        return true;
    }, [interactive, legalTargets, makePlayerMove, askPromotion]);

    // Everything the board says about itself, in one derived object rather
    // than a piece of state that has to be cleared by hand on every path.
    // The checked king goes down first so a selection or capture hint drawn
    // on the same square wins over it: being told what you may do with a
    // piece is more urgent than being told again that you are in check.
    const squareStyles = useMemo(() => {
        const styles: Record<string, React.CSSProperties> = {};
        if (boardStatus.checkedKingSquare) {
            styles[boardStatus.checkedKingSquare] = {
                background: 'var(--sq-check-mark)',
            };
        }
        // A drag in progress owns the hints; otherwise the click selection does.
        const origin = dragFrom ?? selectedSquare;
        if (interactive && origin) {
            // --sq-* from styles/obsidian.css, which defines them per theme,
            // rather than three hexes tuned for the near-black room. A custom
            // property resolves inside an inline style, so the hints re-tune
            // on a theme switch with no re-render.
            styles[origin] = { backgroundColor: 'var(--sq-selected)' };
            for (const target of legalTargets.get(origin) ?? []) {
                styles[target] = {
                    backgroundColor: occupied.has(target) ? 'var(--sq-capture)' : 'var(--sq-legal)',
                };
            }
        }
        return styles;
    }, [boardStatus.checkedKingSquare, interactive, dragFrom, selectedSquare, legalTargets, occupied]);
    // targetColor lets a caller that just changed playerColor (e.g.
    // handleSetColor) tell the poller explicitly which color it's waiting
    // to see on the clock, instead of relying on the `playerColor` closure
    // - which, called synchronously right after setPlayerColorState(), is
    // still bound to the OLD color from this render and hasn't caught up
    // to the state update yet. Without this, switching to Black caused the
    // very first poll to compare the fresh board's "white to move" against
    // the stale old "white" playerColor, match instantly, and report the
    // AI's move as "completed" before it had even started thinking.
    const startAiMovePolling = useCallback((targetColor: 'white' | 'black' = playerColor) => {
        // ----- Cadence -----------------------------------------------------
        //
        // This was a flat `setInterval(..., 1000)`, and that one number was a
        // large part of the tester's "the AI takes too long to play a move".
        // The backend answers a move in about a second; a one-second poll adds
        // a uniformly distributed 0-1000ms of pure waiting on top of it, with
        // an expected cost of ~500ms and a worst case that nearly doubles the
        // wait. None of that is engine time and none of it is context: it is
        // the UI not asking yet.
        //
        // So it ramps. Fast while the answer is plausibly imminent, then
        // backing off so a genuinely slow model reply is not 90 seconds of
        // 8-per-second requests against an endpoint the frontend already polls
        // on its own. `/api/status` is deliberately unlimited in rate_limit.py
        // precisely because the frontend polls it about once a second, so the
        // burst here is inside what that endpoint was built to absorb.
        const delayFor = (n: number) => (n < 10 ? 120 : n < 20 ? 300 : 1000);
        // Wall-clock rather than a poll count, now that polls are not one per
        // second. Gemini calls have been observed taking 20-25s on their own
        // before Stockfish ranking and network overhead, and a UI that gives
        // up a moment before the backend finishes leaves a board stuck on a
        // stale "your turn". 90s is the same headroom the count used to buy.
        const DEADLINE_MS = 90_000;
        const startedAt = performance.now();
        let pollCount = 0;
        let timer: ReturnType<typeof setTimeout> | null = null;
        let stopped = false;
        const stopPolling = () => {
            stopped = true;
            if (timer) clearTimeout(timer);
        };
        const tick = async () => {
            if (stopped) return;
            pollCount++;
            try {
                const response = await apiFetch('/api/status');
                const data = await response.json();
                if (data.success && data.status) {
                    const currentTurn = data.status.turn;
                    // AI move is done once it's the target color's turn again.
                    if (currentTurn === targetColor) {
                        stopPolling();
                        markMoveTiming('AI move seen by the board', startedAt);
                        // Update game state with full data
                        const newGameState = {
                            fen: data.status.fen,
                            turn: data.status.turn,
                            legal_moves: data.status.legal_moves,
                            move_history: data.status.move_history || [],
                            san_history: data.status.san_history || [],
                            is_check: data.status.is_check,
                            is_checkmate: data.status.is_checkmate,
                            is_stalemate: data.status.is_stalemate,
                            is_game_over: data.status.is_game_over,
                            move_count: data.status.move_count,
                            piece_count: data.status.piece_count || 32,
                            castling_rights: data.status.castling_rights || {
                                white_kingside: true,
                                white_queenside: true,
                                black_kingside: true,
                                black_queenside: true
                            }
                        };
                        // CRITICAL: Update chessService with new position
                        chessService.loadPosition(newGameState.fen);
                        setGameState(newGameState);
                        setMoveCount(newGameState.move_count);
                        if (data.eval) {
                            setBoardEval(data.eval);
                        }
                        if (data.history) {
                            setMoveHistory(data.history);
                        }
                        // The coach's explanation of the move it just played
                        // is the newest turn of the transcript that came
                        // back with this same status read.
                        const history = toChatMessages(data.chat_history);
                        if (history) {
                            setChatMessages(history);
                        }
                        setLangflowConfig(prev => ({ ...prev, status: 'connected' }));
                        endMoveTiming('explanation rendered', startedAt);
                        return;
                    }
                }
                if (performance.now() - startedAt >= DEADLINE_MS) {
                    stopPolling();
                    setLangflowConfig(prev => ({ ...prev, status: 'error' }));
                    setChatError('The coach has not moved yet. Try "Make AI move".');
                    return;
                }
            } catch (error) {
                console.error('❌ [AI] Polling error:', error);
                stopPolling();
                return;
            }
            timer = setTimeout(tick, delayFor(pollCount));
        };
        // The first ask is almost immediate. On a fast local backend the move
        // is frequently already there, and the old code's first question was a
        // full second after the player's move landed.
        timer = setTimeout(tick, 90);
    }, [playerColor]);
    // Ongoing poll for AI vs AI auto-play - unlike startAiMovePolling (which
    // polls until exactly one move lands), this keeps polling for as long
    // as the backend reports ai_vs_ai_running, since moves keep happening
    // on their own with no user action in between. Stops itself once the
    // game ends, mode is exited, or auto-play is paused.
    const startAiVsAiPolling = useCallback(() => {
        if (aiVsAiPollRef.current) {
            clearInterval(aiVsAiPollRef.current);
        }
        let lastMoveCount = -1;
        const pollInterval = setInterval(async () => {
            try {
                const response = await apiFetch('/api/status');
                const data = await response.json();
                if (!data.success || !data.status) {
                    return;
                }

                if (data.status.move_count !== lastMoveCount) {
                    lastMoveCount = data.status.move_count;
                    chessService.loadPosition(data.status.fen);
                    setGameState(chessService.getGameState());
                    setMoveCount(data.status.move_count);
                    if (data.eval) {
                        setBoardEval(data.eval);
                    }
                    if (data.history) {
                        setMoveHistory(data.history);
                    }
                    const history = toChatMessages(data.chat_history);
                    if (history) {
                        setChatMessages(history);
                    }
                }

                setAiVsAiRunning(!!data.ai_vs_ai_running);

                if (data.game_mode !== 'ai_vs_ai' || data.status.is_game_over || !data.ai_vs_ai_running) {
                    clearInterval(pollInterval);
                    aiVsAiPollRef.current = null;
                }
            } catch (error) {
                console.error('❌ [AIVAI] Polling error:', error);
            }
        }, 1000);
        aiVsAiPollRef.current = pollInterval;
    }, []);
    const handleReset = async () => {
        const success = await chessService.resetGameOnServer();
        if (success) {
            try {
                const response = await apiFetch('/api/status');
                const data = await response.json();
                if (data.success && data.status) {
                    chessService.loadPosition(data.status.fen);
                    const newGameState = chessService.getGameState();
                    setGameState(newGameState);
                    setMoveCount(0);
                    clearSelection();
                    if (data.eval) {
                        setBoardEval(data.eval);
                    }
                    setMoveHistory(data.history || []);
                    // Server-side chat_history is cleared on every reset -
                    // mirror that here instead of leaving stale messages
                    // from the previous game sitting in the chat tab.
                    setChatMessages([]);
                    setChatError(null);
                    setGameMode('human_vs_ai');
                    setAiVsAiRunning(false);
                    setMoveError(null);
                }
            } catch (error) {
                console.error('❌ [ERROR] Error syncing after reset:', error);
                setMoveError('The game was reset, but the board could not be refreshed.');
            }
        } else {
            // Same reasoning as the move path: a reset that silently did not
            // happen leaves the previous game on screen looking like a bug.
            setMoveError('Could not start a new game - the server did not respond.');
        }
    };
    const handleSetColor = async (color: 'white' | 'black') => {
        setLangflowConfig(prev => ({ ...prev, status: 'idle' }));
        const result = await chessService.setPlayerColor(color);
        if (result.success) {
            setPlayerColorState(color);
            setGameMode('human_vs_ai');
            setAiVsAiRunning(false);
            const newGameState = chessService.getGameState();
            setGameState(newGameState);
            setMoveCount(0);
            clearSelection();
            if (result.eval) {
                setBoardEval(result.eval);
            }
            setMoveHistory(result.history || []);
            // New game, new (empty) server-side chat_history - clear the
            // visible transcript to match instead of leaving the old
            // color's conversation sitting there.
            setChatMessages([]);
            setChatError(null);
            if (typeof result.difficulty === 'number') {
                setDifficulty(result.difficulty);
            }
            if (result.ai_scheduled) {
                setLangflowConfig(prev => ({ ...prev, status: 'thinking' }));
                // Pass `color` explicitly rather than letting the poller
                // default to the `playerColor` closure - see the comment
                // on startAiMovePolling for why that closure is stale here.
                startAiMovePolling(color);
            }
        } else {
            console.error('❌ [COLOR] Failed to switch color:', result);
        }
    };
    const handleStartAiVsAi = async () => {
        const result = await chessService.startAiVsAi();
        if (result.success && result.status) {
            setGameMode('ai_vs_ai');
            setAiVsAiRunning(true);
            chessService.loadPosition(result.status.fen);
            setGameState(chessService.getGameState());
            setMoveCount(0);
            clearSelection();
            if (result.eval) {
                setBoardEval(result.eval);
            }
            setMoveHistory(result.history || []);
            // Fresh AI-vs-AI game also clears server-side chat_history.
            setChatMessages([]);
            setChatError(null);
            setLangflowConfig(prev => ({ ...prev, status: 'connected' }));
            startAiVsAiPolling();
        } else {
            console.error('❌ [AIVAI] Failed to start AI vs AI:', result);
        }
    };
    const handlePauseAiVsAi = async () => {
        const result = await chessService.pauseAiVsAi();
        if (result.success) {
            setAiVsAiRunning(false);
        } else {
            console.error('❌ [AIVAI] Failed to pause:', result);
        }
    };
    const handleResumeAiVsAi = async () => {
        const result = await chessService.resumeAiVsAi();
        if (result.success) {
            setAiVsAiRunning(true);
            startAiVsAiPolling();
        } else {
            console.error('❌ [AIVAI] Failed to resume:', result);
        }
    };
    const handleStepAiVsAi = async () => {
        const result = await chessService.stepAiVsAi();
        if (result.success && result.status) {
            chessService.loadPosition(result.status.fen);
            setGameState(chessService.getGameState());
            setMoveCount(result.status.move_count);
            if (result.eval) {
                setBoardEval(result.eval);
            }
            if (result.history) {
                setMoveHistory(result.history);
            }
            // The step's explanation is a coach turn on the server.
            refreshEval();
        } else {
            console.error('❌ [AIVAI] Failed to step:', result);
        }
    };
    const handleExitAiVsAi = async () => {
        if (aiVsAiPollRef.current) {
            clearInterval(aiVsAiPollRef.current);
            aiVsAiPollRef.current = null;
        }
        const result = await chessService.exitAiVsAi();
        if (result.success) {
            setGameMode('human_vs_ai');
            setAiVsAiRunning(false);
            setLangflowConfig(prev => ({ ...prev, status: 'idle' }));
            try {
                const statusResponse = await apiFetch('/api/status');
                const statusData = await statusResponse.json();
                if (statusData.success && statusData.status) {
                    chessService.loadPosition(statusData.status.fen);
                    setGameState(chessService.getGameState());
                    setMoveCount(statusData.status.move_count);
                    if (statusData.player_color === 'white' || statusData.player_color === 'black') {
                        setPlayerColorState(statusData.player_color);
                    }
                }
            } catch (error) {
                console.error('❌ [AIVAI] Failed to re-sync after exit:', error);
            }
        } else {
            console.error('❌ [AIVAI] Failed to exit:', result);
        }
    };
    const handleMakeAIMove = async () => {
        if (gameMode === 'ai_vs_ai') {
            return;
        }
        const aiColor = playerColor === 'white' ? 'black' : 'white';
        // Re-sync with the server before firing. If a background AI move
        // landed after the polling loop already gave up (e.g. a slow
        // Gemini call finishing just past the timeout window), our local
        // gameState here can be stale - sending a doomed request against a
        // turn that's already moved on is what produced a confusing
        // "Not AI's turn" error while the board had actually advanced.
        try {
            const statusResponse = await apiFetch('/api/status');
            const statusData = await statusResponse.json();
            if (statusData.success && statusData.status) {
                chessService.loadPosition(statusData.status.fen);
                const syncedGameState = chessService.getGameState();
                setGameState(syncedGameState);
                setMoveCount(syncedGameState.move_count);
                if (statusData.eval) {
                    setBoardEval(statusData.eval);
                }
                if (statusData.history) {
                    setMoveHistory(statusData.history);
                }
                if (syncedGameState.turn !== aiColor) {
                    setLangflowConfig(prev => ({ ...prev, status: 'idle' }));
                    return;
                }
                if (syncedGameState.is_game_over) {
                    return;
                }
            }
        } catch (error) {
            console.error('❌ [ERROR] Failed to sync before AI move:', error);
        }
        setLangflowConfig(prev => ({ ...prev, status: 'thinking' }));
        setChatError(null);
        try {
            const response = await apiFetch('/api/ai-move', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                }
            });
            const result = await response.json();
            if (result.success) {
                // `/api/ai-move`'s game_state is a SHORT one - fen, turn,
                // legal_moves and the four status booleans, and none of
                // move_history / san_history / move_count / piece_count /
                // castling_rights. Assigning it wholesale (which is what
                // this did) left `move_count` undefined, so the strip under
                // the board read "undefined moves" for the rest of the game,
                // and left chessService holding the pre-move position.
                //
                // Load the position, take the full local shape from it, and
                // let the server's own booleans and legal move list win on
                // top - python-chess is the authority on all six of those.
                const serverState = result.game_state;
                chessService.loadPosition(serverState.fen);
                const newGameState: GameState = {
                    ...chessService.getGameState(),
                    ...serverState,
                    move_count: (result.history?.length ?? moveCount + 1),
                };
                setGameState(newGameState);
                setMoveCount(newGameState.move_count);
                clearSelection();
                if (result.eval) {
                    setBoardEval(result.eval);
                }
                if (result.history) {
                    setMoveHistory(result.history);
                }
                // Its reasoning is a coach turn on the server's transcript.
                refreshEval();
                setLangflowConfig(prev => ({ ...prev, status: 'connected' }));
            } else {
                // Show detailed error information
                const errorMsg = result.message || 'Unknown error';
                const errorDetails = result.error_type?.error || '';
                const reasoning = result.error_type?.reasoning || '';
                let fullErrorMsg = `${errorMsg}`;
                if (errorDetails) {
                    fullErrorMsg += `\n\nDetails: ${errorDetails}`;
                }
                if (reasoning) {
                    fullErrorMsg += `\n\nAI response: ${reasoning}`;
                }
                setChatError(fullErrorMsg);
                setLangflowConfig(prev => ({ ...prev, status: 'error' }));
            }
        } catch (error) {
            console.error('❌ [ERROR] Network error making AI move:', error);
            const errorMessage = error instanceof Error ? error.message : 'Unknown error';
            const networkError = `Network error: ${errorMessage}`;
            setChatError(networkError);
            setLangflowConfig(prev => ({ ...prev, status: 'error' }));
        }
    };
    // Typed on the two elements that can emit it rather than on <input>: the
    // control is a <select> now (it was a range slider in the old left rail),
    // and the union keeps this honest if it ever goes back.
    const handleDifficultyChange = async (
        event: React.ChangeEvent<HTMLSelectElement | HTMLInputElement>,
    ) => {
        const newDifficulty = parseInt(event.target.value, 10);
        setDifficulty(newDifficulty); // optimistic, so the control feels instant
        try {
            const response = await apiFetch('/api/difficulty', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ difficulty: newDifficulty })
            });
            const result = await response.json();
            if (result.success) {
                setDifficulty(result.difficulty);
            } else {
                console.error('❌ [DIFFICULTY] Server rejected difficulty change:', result);
            }
        } catch (error) {
            console.error('❌ [DIFFICULTY] Network error setting difficulty:', error);
        }
    };
    const chatListRef = useRef<HTMLDivElement>(null);
    useEffect(() => {
        if (chatListRef.current) {
            chatListRef.current.scrollTop = chatListRef.current.scrollHeight;
        }
    }, [chatMessages, chatPending, chatError, langflowConfig.status]);
    const handleSendChatMessage = async (event: React.FormEvent) => {
        event.preventDefault();
        const message = chatInput.trim();
        if (!message || chatSending) {
            return;
        }
        setChatInput('');
        setChatPending(message);
        setChatError(null);
        try {
            const response = await apiFetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message })
            });
            const data = await response.json();
            if (data.success) {
                // The server's transcript now ends with this question and
                // its answer; take the whole thing rather than appending,
                // so a coach turn that landed meanwhile is in its place.
                const history = toChatMessages(data.history ?? data.data?.history);
                if (history) {
                    setChatMessages(history);
                } else {
                    const reply = data.reply ?? data.data?.reply ?? '(no reply)';
                    setChatMessages(prev => [...prev, { role: 'user', text: message }, { role: 'ai', text: reply }]);
                }
            } else {
                const errorText = data.error_type?.message || data.error_type?.error || data.message || 'Chat request failed';
                setChatInput(message);
                setChatError(errorText);
            }
        } catch (error) {
            console.error('❌ [CHAT] Network error sending chat message:', error);
            setChatInput(message);
            setChatError('Could not reach the coach. Check the connection and ask again.');
        } finally {
            setChatPending(null);
        }
    };
    // Empties the conversation on both sides. Only reached from the Actions
    // tab: the coach's context for the next question goes with it, which is
    // the point, and why a client-only clear would have been a lie.
    const handleClearChat = async () => {
        setChatError(null);
        try {
            const response = await apiFetch('/api/chat/clear', { method: 'POST' });
            const data = await response.json();
            if (data.success) {
                setChatMessages([]);
            } else {
                setChatError('Could not clear the conversation.');
            }
        } catch {
            setChatError('Could not clear the conversation.');
        }
    };
    // These three walk the whole history. The component re-renders on a 1s
    // poll while the AI is thinking, so without memos they re-derived the move
    // pairs and both accuracy summaries roughly ninety times per AI move.
    const movePairs = React.useMemo(() => buildMovePairs(moveHistory), [moveHistory]);
    // The badge that sits on the board tracks only the most recent move -
    // the same way chess.com shows one grade on the square just played to,
    // rather than littering the board with every past move's verdict.
    // White plays the even plies, Black the odd ones.
    const whiteStats = React.useMemo(
        () => summarizeSide(moveHistory.filter((_, i) => i % 2 === 0)), [moveHistory]);
    const blackStats = React.useMemo(
        () => summarizeSide(moveHistory.filter((_, i) => i % 2 === 1)), [moveHistory]);
    const gradeRows = QUALITY_ORDER.filter(
        label => (whiteStats.counts[label] ?? 0) + (blackStats.counts[label] ?? 0) > 0
    );
    // How much of this breakdown is a close call, and what depth produced it.
    //
    // The per-move grades hedge themselves (see gradeSentence), and the panel
    // that aggregates them did not - so a column of counts read as harder
    // evidence than the moves it was counting. `confidence` is "low" when the
    // centipawn loss sits inside the engine's own measured run-to-run spread of
    // a threshold, which is a fact about the search rather than about the
    // player, and the honest place to say it is next to the totals.
    const gradedMoves = React.useMemo(
        () => moveHistory.map(entry => entry.quality).filter(Boolean) as MoveQuality[],
        [moveHistory],
    );
    const closeCalls = gradedMoves.filter(q => q.confidence === 'low').length;
    const gradingDepth = gradedMoves.find(q => q.depth)?.depth ?? null;
    const lastEntry = moveHistory.length > 0 ? moveHistory[moveHistory.length - 1] : null;
    const lastQuality = lastEntry?.quality ?? null;
    const lastMoveTarget = lastEntry?.move?.slice(2, 4) ?? null;
    // Whose half-move the badge is grading. White plays the even plies.
    const lastMover: 'white' | 'black' = moveHistory.length % 2 === 1 ? 'white' : 'black';
    const badgePlacement = (() => {
        if (!showMoveQuality || !lastQuality || !lastMoveTarget) return null;
        if (UNBADGED_LABELS.has(lastQuality.label)) return null;
        const file = lastMoveTarget.charCodeAt(0) - 'a'.charCodeAt(0);
        const rank = parseInt(lastMoveTarget[1], 10);
        if (Number.isNaN(rank) || file < 0 || file > 7 || rank < 1 || rank > 8) return null;
        // The board is drawn from the side the human is playing, so the
        // square-to-pixel mapping flips with the orientation.
        const col = playerColor === 'white' ? file : 7 - file;
        const row = playerColor === 'white' ? 8 - rank : rank - 1;
        const squareSize = boardSize / 8;
        // .chess-board-wrapper carries 20px of padding before the board
        // itself starts; the badge is positioned against the wrapper.
        const BOARD_INSET = 20;
        return {
            left: BOARD_INSET + col * squareSize + squareSize * 0.66,
            top: BOARD_INSET + row * squareSize - squareSize * 0.16,
            size: Math.max(18, squareSize * 0.46)
        };
    })();
    // Builds a renderer per piece type. Rebuilding that object on every render
    // handed react-chessboard a new customPieces prop each time, which is the
    // one prop that makes it re-render all 32 squares. Sandbox.tsx already
    // memoised this; the real game did not.
    const customPieces = React.useMemo(() => getCustomPieces(pieceTheme), [pieceTheme]);
    // Player strips sit above and below the board and answer, without the
    // reader moving their eyes: whose turn it is, how accurately each side
    // has played so far, and who is up material. Accuracy comes from the
    // same /api/status payload the Review panel uses, so the two can never
    // disagree. There is deliberately no clock - the backend has no timer,
    // and a fake one would be the only lie on the screen.
    const aiColor: 'white' | 'black' = playerColor === 'white' ? 'black' : 'white';
    const renderPlayerStrip = (side: 'white' | 'black', who: 'you' | 'ai') => {
        const stats = accuracySummary ? accuracySummary[side] : null;
        const taken = capturedBy(gameState.fen, side);
        const edge = materialEdge(gameState.fen, side);
        const isTurn = gameState.turn === side && !gameState.is_game_over;
        const thinking = who === 'ai' && langflowConfig.status === 'thinking';
        return (
            <div className={`player-strip ${isTurn ? 'is-turn' : ''} ${thinking ? 'is-thinking' : ''}`}>
                <span className={`player-disc ${side}`} aria-hidden="true" />
                <span className="player-identity">
                    <span className="player-name">{who === 'you' ? 'You' : 'Gemini'}</span>
                    <span className="player-sub">
                        {side === 'white' ? 'White' : 'Black'}
                        {who === 'ai' && ` \u00b7 difficulty ${difficulty}`}
                        {thinking && ' \u00b7 thinking\u2026'}
                    </span>
                </span>
                {taken.length > 0 && (
                    <span className="player-captures" title="Pieces captured">
                        {taken.map((k, i) => (
                            <span key={`${k}-${i}`}>{CAPTURED_GLYPH[k]}</span>
                        ))}
                        {edge > 0 && <em className="player-edge">+{edge}</em>}
                    </span>
                )}
                {stats && stats.accuracy !== null && (
                    <span className="player-accuracy" title={`${stats.graded} moves graded`}>
                        <em>{stats.accuracy}%</em>
                        <span>accurate</span>
                    </span>
                )}
            </div>
        );
    };
    return (
        <div
            className="chess-container"
            /* The coaching column is capped to the board's height so it can
               never hang below it. boardSize is responsive, so the cap has
               to travel with it rather than being a magic number in CSS. */
            /* Published so the shell can size the board's track, the frame and
               everything under the board from the one number the board itself
               is rendered at. */
            style={{ ['--board-size' as string]: `${boardSize}px` } as React.CSSProperties}
        >
            <div className="board-area">
                {/* The identity row, the same one Learn and Review carry. New
                    game sits in its exit slot beside Review's "Close game":
                    both throw away the thing on screen, and neither is what you
                    came here to do, so both are quiet. */}
                <div className="game-identity">
                    <div className="game-identity-row">
                        <h2 className="game-title">
                            Playing <span className="game-vs">Gemini</span>
                        </h2>
                        <button
                            type="button"
                            className="ws-exit"
                            onClick={handleReset}
                            title="Abandon this game and start a new one"
                        >
                            New game
                        </button>
                    </div>
                    <span className="game-subtitle">
                        {[
                            `You are ${playerColor === 'white' ? 'White' : 'Black'}`,
                            difficultyLabel(difficulty),
                            difficultyBand(difficulty).blurb,
                        ].join(' \u00b7 ')}
                    </span>
                </div>

                <div className="board-column" ref={boardColumnRef}>
                    <div className="board-row">
                        <div className="board-stack">
                        {renderPlayerStrip(aiColor, 'ai')}
                        <div className={`chess-board-wrapper ${langflowConfig.status === 'thinking' ? 'ai-thinking' : ''}`}>
                            {langflowConfig.status === 'thinking' && (
                                <div className="ai-thinking-indicator">
                                    <div className="thinking-dots">
                                        <span></span>
                                        <span></span>
                                        <span></span>
                                    </div>
                                </div>
                            )}
                            <Chessboard
                                position={gameState.fen}
                                onSquareClick={onSquareClick}
                                customSquareStyles={squareStyles}
                                boardWidth={boardSize}
                                showBoardNotation={showCoordinates}
                                customNotationStyle={BOARD_NOTATION_STYLE}
                                boardOrientation={playerColor}
                                // Drag is a SECOND way to play, not a
                                // replacement for onSquareClick above it -
                                // both are always live, both read
                                // legalTargets, and there is no toggle.
                                arePiecesDraggable={interactive}
                                isDraggablePiece={isDraggablePiece}
                                onPieceDragBegin={onPieceDragBegin}
                                onPieceDragEnd={onPieceDragEnd}
                                onPieceDrop={onPieceDrop}
                                // Auto-queen, matching what click-to-move
                                // has always done here: without this
                                // react-chessboard opens its own promotion
                                // dialog on a drag to the last rank, so the
                                // same move would ask a question one way and
                                // not the other.
                                autoPromoteToQueen
                                // Pieces (ours and the AI's) now slide to their new
                                // square instead of snapping - react-chessboard
                                // animates any position-prop change on its own as
                                // long as this is > 0, including AI moves that
                                // arrive via polling rather than a drag.
                                animationDuration={300}
                                customPieces={customPieces}
                                // Square colours come from the theme tokens, not
                                // from a JS constant: react-chessboard writes
                                // these straight into an inline style, and a CSS
                                // custom property resolves there, so the board
                                // re-colours on a theme switch with no re-render
                                // and no listener. A board tuned for a near-black
                                // room is muddy on paper - see --board-* in
                                // styles/obsidian.css.
                                customDarkSquareStyle={{ backgroundColor: 'var(--board-dark)' }}
                                customLightSquareStyle={{ backgroundColor: 'var(--board-light)' }}
                            />
                            {badgePlacement && lastQuality && (
                                <div
                                    className="move-quality-badge"
                                    style={{
                                        left: badgePlacement.left,
                                        top: badgePlacement.top,
                                        width: badgePlacement.size,
                                        height: badgePlacement.size,
                                        fontSize: badgePlacement.size * 0.5,
                                        backgroundColor: qualityColor(lastQuality.label)
                                    }}
                                    /* Says WHOSE move it grades, which it did
                                       not. The badge tracks the most recent
                                       half-move; the AI answers in about a
                                       second; so the badge a player sees a
                                       moment after moving is usually the grade
                                       of the AI's REPLY, on the AI's square. A
                                       "??" appearing just after your own move
                                       with nothing on it to say whose it is
                                       reads as a verdict on your move - one
                                       way "I played the best move and it
                                       called it bad" happens with no grade
                                       being wrong at all. It also carries the
                                       engine's own hedge when the evidence is
                                       thin; see gradeSentence. */
                                    title={gradeSentence(lastQuality, lastMover)}
                                    aria-label={gradeSentence(lastQuality, lastMover)}
                                >
                                    {lastQuality.symbol}
                                    {/* A disc in the mover's colour in the
                                        badge's corner. There is room for one
                                        glyph on a square, so the answer to
                                        "whose?" has to be a colour rather than
                                        a word - and it is the same white/black
                                        disc the player strips above and below
                                        the board already use. */}
                                    <span
                                        className={`move-quality-who ${lastMover}`}
                                        aria-hidden="true"
                                    />
                                </div>
                            )}
                            {/* The end-state layer belongs to the board
                                frame, not to the page: trap 11 in CLAUDE.md
                                is what happens to anything in this column
                                that is sized against something other than
                                the board. `inset: 0` in here cannot reach
                                the coaching panel at any viewport.

                                Its reset IS the mode's reset - handleReset,
                                the same function "New game" above the board
                                calls - under the same label, so the action
                                has one name and one implementation. */}
                            <BoardEndState
                                end={boardStatus.end}
                                onReset={handleReset}
                                resetLabel="New game"
                            />
                            {/* Beside the board, the board's height, in the
                                slot the column keeps for it - see EvalBar. */}
                            <EvalBar
                                share={evalToWhitePercent(boardEval) / 100}
                                label={formatEval(boardEval)}
                                on={showEngineNumbers}
                                flipped={playerColor === 'black'}
                            />
                            {/* Asked on the square being promoted to, inside
                                the board frame - so it cannot reach the
                                coaching panel or the transport at any width.
                                Cancelling leaves the pawn where it was and the
                                position untouched. */}
                            <PromotionPicker
                                pending={pendingPromotion}
                                boardSize={boardSize}
                                orientation={playerColor}
                                pieceTheme={pieceTheme}
                                onSelect={piece => {
                                    const move = pendingPromotion;
                                    setPendingPromotion(null);
                                    if (move) {
                                        void makePlayerMove(
                                            move.from as Square,
                                            move.to as Square,
                                            piece,
                                        );
                                    }
                                }}
                                onCancel={() => setPendingPromotion(null)}
                            />
                        </div>
                        {renderPlayerStrip(playerColor, 'you')}
                        </div>
                    </div>

                    {/* One line under the board: where the game is on the
                        left, what the position is doing on the right. The
                        same shape Learn and Review use, and always present so
                        an arriving check does not shove the transport down a
                        row as you reach for it.

                        Whose turn it is is NOT repeated here. The player
                        strips say it, by which of the two is lit, which is
                        how a chess clock says it. */}
                    <div className="game-strip">
                        <span className="game-strip-where">
                            {moveCount} {moveCount === 1 ? 'move' : 'moves'}
                        </span>
                        {/* The eval bar used to be on this row. It is beside
                            the board now (EvalBar, inside the frame above),
                            where the column reserves its slot - see
                            --ws-eval-slot in styles/shell.css for why that no
                            longer pushes the board the way a bar beside it
                            once did. */}
                        <span
                            className={`game-alert ${
                                moveError ? 'is-danger'
                                    : boardStatus.isCheckmate ? 'is-danger'
                                        : boardStatus.gameOver ? 'is-warn'
                                            : boardStatus.inCheck ? 'is-warn'
                                                : 'is-quiet'
                            }`}
                            role="status"
                            aria-live="polite"
                            title={moveError ?? undefined}
                        >
                            {/* Same reading as the board's red king square
                                and the end-state layer - one object, three
                                treatments, so they cannot contradict. One
                                word, not the overlay's sentence: this strip
                                is one line and a wrap here costs the board
                                43px (see CLAUDE.md 11). */}
                            {/* A failure outranks the position: the user is
                                waiting to find out why the board did not
                                move, and the position has not changed. */}
                            {moveError ? moveError
                                : boardStatus.isCheckmate ? 'Checkmate'
                                    : boardStatus.isStalemate ? 'Stalemate'
                                        : boardStatus.gameOver ? 'Draw'
                                            : boardStatus.inCheck ? 'Check'
                                                : AI_STATUS_TEXT[langflowConfig.status] ?? 'Coach ready'}
                        </span>
                    </div>

                    {/* The transport. These were four buttons in a 2x2 grid
                        inside a card to the left of the board, which is where
                        the primary action of the whole mode was living: in the
                        middle of a settings stack, in a column the board was
                        competing with. They are the board's own controls, so
                        they are under the board, evenly shared, with the one
                        that moves the game marked as primary. */}
                    {gameMode === 'human_vs_ai' ? (
                        <div className="game-transport">
                            {!gameState.is_game_over && (
                                <button
                                    onClick={handleMakeAIMove}
                                    className="action-btn ai-move-btn"
                                    /* Disabled when it is not the AI's turn.
                                       handleMakeAIMove already refuses in that
                                       case - it re-syncs with the server and
                                       returns silently - so before this the
                                       button was enabled, prominent, and did
                                       NOTHING on a fresh game, where it is
                                       always your move first. A control that
                                       looks live and answers nothing is worse
                                       than one that is visibly unavailable.
                                       The handler keeps its own check: it
                                       guards against the board having moved on
                                       since this rendered, which is a
                                       different problem. */
                                    disabled={
                                        langflowConfig.status === 'thinking'
                                        || gameState.turn === playerColor
                                    }
                                    title={
                                        gameState.turn === playerColor
                                            ? 'It is your move - play one, and the coach will answer'
                                            : 'Have the coach play its move now'
                                    }
                                >
                                    {langflowConfig.status === 'thinking' ? 'Thinking…' : (<>
                                        <span className="btn-label-full">Make AI move</span>
                                        <span className="btn-label-tight">AI move</span>
                                    </>)}
                                </button>
                            )}
                            <button
                                onClick={() => handleSetColor(playerColor === 'white' ? 'black' : 'white')}
                                className="action-btn color-switch-btn"
                                disabled={langflowConfig.status === 'thinking'}
                            >
                                <span className="btn-label-full">Play as </span>{playerColor === 'white' ? 'Black' : 'White'}
                            </button>
                        </div>
                    ) : (
                        <div className="game-transport">
                            {aiVsAiRunning ? (
                                <button onClick={handlePauseAiVsAi} className="action-btn pause-btn">
                                    Pause
                                </button>
                            ) : (
                                <>
                                    <button
                                        onClick={handleResumeAiVsAi}
                                        className="action-btn resume-btn"
                                        disabled={gameState.is_game_over}
                                    >
                                        Resume
                                    </button>
                                    <button
                                        onClick={handleStepAiVsAi}
                                        className="action-btn step-btn"
                                        disabled={gameState.is_game_over}
                                    >
                                        Next move
                                    </button>
                                </>
                            )}
                            <button onClick={handleExitAiVsAi} className="action-btn exit-btn">
                                Stop watching
                            </button>
                        </div>
                    )}

                    {/* The meta row: what the board shows, and how hard the
                        coach plays. Settings, not moves - so they are quiet,
                        on one line, and below the controls that do something.

                        Difficulty is the same <select> Learner Mode uses, on
                        the same five named bands from difficulty.ts. It was a
                        180px card here with a slider, a band name, two end
                        labels and a blurb - the largest single object in the
                        old left rail, for a setting most people touch once.
                        The blurb is not lost: it is in the subtitle at the top
                        of the mode, where it is read once and then ignored. */}
                    {/* The meta row holds the one setting that changes how the
                        game is played: how hard the coach plays. The display
                        switches (engine numbers, coordinates) and the board
                        size went to the Actions tab - they are set once and
                        left, and every one of them under the board was a row
                        the board had to be shrunk to make room for. Alone on
                        the row, the select gets its name back. */}
                    <div className="ws-meta">
                        <span className="ws-meta-spacer" />
                        <label className="game-difficulty">
                            <span className="ws-label-full">Difficulty</span>
                            <select
                                aria-label="Engine strength"
                                value={difficulty}
                                onChange={handleDifficultyChange}
                                title={difficultyBand(difficulty).blurb}
                            >
                                {DIFFICULTY_LEVELS.map(value => (
                                    <option key={value} value={value}>
                                        {difficultyLabel(value)}
                                    </option>
                                ))}
                            </select>
                        </label>
                    </div>
                </div>
                <div className="ai-column">
                <div className="rail-canvas-panel">
                    {/* The tab strip reads before the panel it switches,
                       because that is the order it is used in. It used to be
                       written after it and dragged above with `order: -1`,
                       which left the markup saying the opposite of the
                       screen - and put the tabs after the panel for anyone
                       navigating by keyboard or screen reader. */}
                    <div className="rail-stack" role="tablist" aria-label="Coaching panel">
                        {RAIL_SECTIONS.map(section => (
                            <button
                                key={section.id}
                                type="button"
                                role="tab"
                                id={`rail-tab-${section.id}`}
                                aria-controls="rail-panel"
                                aria-selected={activeSection === section.id}
                                title={section.label}
                                className={`rail-icon-btn ${activeSection === section.id ? 'active' : ''}`}
                                onClick={() => handleSectionClick(section.id)}
                            >
                                <span className="rail-label">{section.label}</span>
                                {section.id === 'chat' && langflowConfig.status === 'thinking' && (
                                    <span className="rail-thinking-dot" aria-hidden="true" />
                                )}
                                {unreadSections[section.id] && activeSection !== section.id && (
                                    <span className="rail-unread-dot" aria-hidden="true" />
                                )}
                            </button>
                        ))}
                    </div>
                    <div
                        className="rail-canvas-content"
                        id="rail-panel"
                        role="tabpanel"
                        aria-labelledby={`rail-tab-${activeSection}`}
                        tabIndex={0}
                    >
                        {activeSection === 'review' && (
                            <div className="rail-canvas-inner fade-slide-in">
                                {!showMoveQuality ? (
                                    <EmptyState title="Move grading is off">
                                        Turn on <strong>Grade</strong> in the Moves
                                        panel and accuracy will build up from your
                                        next move.
                                    </EmptyState>
                                ) : (
                                    <div className="review-content">
                                        <div className="review-accuracy-row">
                                            {([['white', whiteStats], ['black', blackStats]] as const).map(([side, stats]) => (
                                                <div className="review-accuracy-card" key={side}>
                                                    <span className="review-accuracy-side">
                                                        {side === 'white' ? '♔' : '♚'} {playerColor === side ? 'You' : 'AI'}
                                                    </span>
                                                    <span className="review-accuracy-value">
                                                        {stats.accuracy !== null ? `${stats.accuracy}%` : '--'}
                                                    </span>
                                                    <span className="review-accuracy-sub">
                                                        {stats.graded > 0 ? `${stats.graded} judged` : 'no data'}
                                                    </span>
                                                </div>
                                            ))}
                                        </div>
                                        {gradeRows.length === 0 ? (
                                            <EmptyState title="No graded moves yet">
                                                Accuracy appears once each side has
                                                played a move.
                                            </EmptyState>
                                        ) : (
                                            <div className="review-breakdown">
                                                {/* Counts are split per side, matching the two accuracy
                                                    cards above - a single whole-game tally couldn't tell
                                                    you whose blunders they were. */}
                                                <div className="review-breakdown-head">
                                                    <span />
                                                    <span />
                                                    <span>{playerColor === 'white' ? 'You' : 'AI'}</span>
                                                    <span>{playerColor === 'black' ? 'You' : 'AI'}</span>
                                                </div>
                                                {gradeRows.map(label => (
                                                    <div className="review-breakdown-row" key={label}>
                                                        <span
                                                            className="review-breakdown-dot"
                                                            style={{ backgroundColor: qualityColor(label) }}
                                                        />
                                                        <span className="review-breakdown-label">{label}</span>
                                                        <span className="review-breakdown-count">
                                                            {whiteStats.counts[label] ?? 0}
                                                        </span>
                                                        <span className="review-breakdown-count">
                                                            {blackStats.counts[label] ?? 0}
                                                        </span>
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                        <button
                                            type="button"
                                            className="review-regrade-btn"
                                            onClick={handleRegrade}
                                            disabled={regrading || moveHistory.length === 0}
                                            title="Grade any moves that were played while grading was switched off"
                                        >
                                            {regrading
                                                ? `Grading… ${accuracySummary?.regrade?.done ?? 0}/${accuracySummary?.regrade?.total ?? 0}`
                                                : 'Grade missing moves'}
                                        </button>
                                        <p className="review-note">
                                            Accuracy is the average win-percentage kept per judged move. Book and
                                            forced moves are excluded - neither reflects a choice made at the board.
                                            {gradingDepth && ` Graded by Stockfish at depth ${gradingDepth}.`}
                                        </p>
                                        {/* Said here because the per-move
                                            grades already say it and the totals
                                            did not, which made a column of
                                            counts read as firmer evidence than
                                            the moves being counted. */}
                                        {closeCalls > 0 && (
                                            <p className="review-note">
                                                {closeCalls === 1
                                                    ? 'One of these was a close call'
                                                    : `${closeCalls} of these were close calls`}
                                                {' '}at this depth - the engine could not separate the move from
                                                the grade next door, so it was given the kinder one. Hover a move
                                                to see which.
                                            </p>
                                        )}
                                    </div>
                                )}
                            </div>
                        )}
                        {activeSection === 'learning' && (
                            <div className="rail-canvas-inner fade-slide-in" key={`learning-panel-${learningUpdateKey}`}>
                                {learningSummary ? (
                                    <div className="learning-content">
                                        {learningSummary.opponent?.games_played > 0 ? (
                                            <>
                                                <p>
                                                    Opponent record: {learningSummary.opponent.wins}W / {learningSummary.opponent.losses}L / {learningSummary.opponent.draws}D
                                                    {' '}across {learningSummary.opponent.games_played} tracked game{learningSummary.opponent.games_played === 1 ? '' : 's'}.
                                                </p>
                                                {learningSummary.opponent.blunder_rate !== null && learningSummary.opponent.blunder_rate !== undefined && (
                                                    <p>Blunder rate: {(learningSummary.opponent.blunder_rate * 100).toFixed(0)}%</p>
                                                )}
                                                {learningSummary.opponent.top_openings?.length > 0 && (
                                                    <p>Favorite opening start: {learningSummary.opponent.top_openings[0].moves}</p>
                                                )}
                                            </>
                                        ) : (
                                            <p>No tracked game history yet - this fills in as you play more games.</p>
                                        )}
                                        {learningSummary.ai_self?.total_ai_moves > 0 && (
                                            <p>
                                                AI self: {learningSummary.ai_self.gemini_moves} Gemini-chosen move{learningSummary.ai_self.gemini_moves === 1 ? '' : 's'}
                                                {learningSummary.ai_self.gemini_win_rate !== null && learningSummary.ai_self.gemini_win_rate !== undefined && (
                                                    <> ({(learningSummary.ai_self.gemini_win_rate * 100).toFixed(0)}% win rate)</>
                                                )}
                                            </p>
                                        )}
                                    </div>
                                ) : (
                                    <EmptyState title="Loading your progress" tone="thinking">
                                        Reading what previous games recorded.
                                    </EmptyState>
                                )}
                            </div>
                        )}
                        {activeSection === 'chat' && (
                            <div className="rail-canvas-inner chat-canvas-inner fade-slide-in" key="chat-panel">
                                <div className="chat-messages" ref={chatListRef}>
                                    {chatMessages.length === 0 && !chatPending && langflowConfig.status !== 'thinking' && (
                                        <EmptyState title="Waiting for your first move">
                                            I'll explain each move I play here, and you can
                                            ask about the position, its plans, or what I
                                            expect you to play - "why?" works too.
                                        </EmptyState>
                                    )}
                                    {chatMessages.map((msg, i) => (
                                        <div
                                            key={i}
                                            className={`chat-message chat-message-${msg.role}${msg.move ? ' chat-message-coach' : ''}`}
                                        >
                                            {renderFormattedText(msg.text)}
                                        </div>
                                    ))}
                                    {chatPending && (
                                        <div className="chat-message chat-message-user">{chatPending}</div>
                                    )}
                                    {/* One pending bubble for both things the coach
                                        can be doing - answering a question, or
                                        choosing and explaining a move - because
                                        both end the same way: a bubble here. */}
                                    {(chatSending || langflowConfig.status === 'thinking') && (
                                        <div className="chat-message chat-message-ai chat-message-pending" role="status" aria-live="polite">
                                            <span className="thinking-dots mini" aria-label={chatSending ? 'The coach is answering' : 'The coach is choosing a move'}>
                                                <span></span><span></span><span></span>
                                            </span>
                                        </div>
                                    )}
                                    {chatError && (
                                        <p className="chat-error" role="alert">{chatError}</p>
                                    )}
                                </div>
                                <form className="chat-input-row" onSubmit={handleSendChatMessage}>
                                    <input
                                        type="text"
                                        value={chatInput}
                                        onChange={(e: React.ChangeEvent<HTMLInputElement>) => setChatInput(e.target.value)}
                                        placeholder="e.g. why did you take there?"
                                        disabled={chatSending}
                                        className="chat-input"
                                        aria-label="Chat message"
                                    />
                                    <button type="submit" className="action-btn chat-send-btn" disabled={chatSending || !chatInput.trim()}>
                                        Send
                                    </button>
                                </form>
                            </div>
                        )}
                        {activeSection === 'actions' && (
                            <div className="rail-canvas-inner fade-slide-in" key="actions-panel">
                                <div className="actions-list">
                                    {/* Each is the same control it was under the
                                        board, with its explanation beside it now
                                        that there is room for one. */}
                                    <div className="actions-item">
                                        <button
                                            type="button"
                                            onClick={handleStartAiVsAi}
                                            className="action-btn ai-vs-ai-btn"
                                            disabled={langflowConfig.status === 'thinking' || gameMode === 'ai_vs_ai'}
                                        >
                                            Watch AI play
                                        </button>
                                        <span className="actions-note">
                                            {gameMode === 'ai_vs_ai'
                                                ? 'Running - the controls under the board pause, step and stop it.'
                                                : 'Start a fresh game and let the coach play both sides.'}
                                        </span>
                                    </div>
                                    <label className="game-switch actions-item">
                                        <input
                                            type="checkbox"
                                            checked={showEngineNumbers}
                                            onChange={() => setShowEngineNumbers(v => !v)}
                                        />
                                        <span>Engine numbers</span>
                                        <span className="actions-note">Evaluation bar and scores under the board. Off by default while you learn.</span>
                                    </label>
                                    <label className="game-switch actions-item">
                                        <input
                                            type="checkbox"
                                            checked={showCoordinates}
                                            onChange={() => setShowCoordinates(v => !v)}
                                        />
                                        <span>Coordinates</span>
                                        <span className="actions-note">Letters and numbers along the board's edges.</span>
                                    </label>
                                    <div className="actions-item">
                                        <BoardSizeControl value={boardSizePref} onChange={setBoardSizePref} />
                                        <span className="actions-note">How large the board is drawn. Auto follows the window.</span>
                                    </div>
                                    <div className="actions-item">
                                        <button
                                            type="button"
                                            onClick={() => void handleClearChat()}
                                            className="action-btn actions-clear-chat"
                                            disabled={chatSending || chatMessages.length === 0}
                                        >
                                            Clear chat
                                        </button>
                                        <span className="actions-note">
                                            Empties the conversation. The coach forgets it too; the game stays.
                                        </span>
                                    </div>
                                </div>
                            </div>
                        )}
                        {activeSection === 'theme' && (
                            <div className="rail-canvas-inner fade-slide-in" key="theme-panel">
                                <div className="theme-picker-list">
                                    {PIECE_THEME_LIST.map(theme => {
                                        const swatchColors = getBoardColors(theme.id);
                                        return (
                                            <button
                                                key={theme.id}
                                                onClick={() => setPieceTheme(theme.id)}
                                                className={`theme-picker-option ${pieceTheme === theme.id ? 'active' : ''}`}
                                            >
                                                <span
                                                    className="theme-picker-swatch"
                                                    style={{
                                                        background: swatchColors
                                                            ? `linear-gradient(135deg, ${swatchColors.light} 50%, ${swatchColors.dark} 50%)`
                                                            : 'linear-gradient(135deg, #f0d9b5 50%, #b58863 50%)'
                                                    }}
                                                />
                                                {theme.label}
                                            </button>
                                        );
                                    })}
                                </div>
                            </div>
                        )}
                    </div>
                </div>
                    <div className="moves-panel">
                        {/* The grading toggle lives in the Moves header
                            rather than the control panel: it's a property of
                            how moves are displayed, it sits directly above
                            the list it annotates, and the header had spare
                            room - so nothing else on screen has to move. */}
                        <div className="moves-panel-header">
                            <h3 className="moves-panel-title">Moves</h3>
                            <button
                                type="button"
                                className={`quality-toggle ${showMoveQuality ? 'quality-toggle-on' : ''}`}
                                onClick={toggleMoveQuality}
                                role="switch"
                                aria-checked={showMoveQuality}
                                title={showMoveQuality
                                    ? 'Move grading on - click to turn off (also stops the extra engine analysis)'
                                    : 'Move grading off - click to turn on'}
                            >
                                <span className="quality-toggle-label">Grade</span>
                                <span className="quality-toggle-track">
                                    <span className="quality-toggle-thumb" />
                                </span>
                            </button>
                        </div>
                        <div className="move-history-list" ref={moveHistoryListRef}>
                            {movePairs.length === 0 && (
                                <EmptyState title="No moves yet">
                                    Every move you and the coach play is listed
                                    here, newest last.
                                </EmptyState>
                            )}
                            {movePairs.map(pair => (
                                <div key={pair.moveNumber} className="move-history-row">
                                    <span className="move-history-number">{pair.moveNumber}.</span>
                                    <span className={`move-history-white ${pair.isLastWhite ? 'move-history-current' : ''}`}>
                                        {pair.white}
                                        {showMoveQuality && pair.whiteQuality && !UNBADGED_LABELS.has(pair.whiteQuality.label) && (
                                            <span
                                                className="move-quality-tag"
                                                style={{ color: qualityColor(pair.whiteQuality.label) }}
                                                /* The full sentence, hedged as
                                                   far as the evidence allows -
                                                   the bare name asserted the
                                                   label with a confidence the
                                                   search does not always have.
                                                   See gradeSentence. */
                                                title={gradeSentence(pair.whiteQuality, 'white')}
                                            >
                                                {pair.whiteQuality.symbol}
                                            </span>
                                        )}
                                    </span>
                                    <span
                                        className={`move-history-black ${pair.isLastBlack ? 'move-history-current' : ''}`}
                                        title={pair.blackExplanation || undefined}
                                    >
                                        {pair.black}
                                        {showMoveQuality && pair.blackQuality && !UNBADGED_LABELS.has(pair.blackQuality.label) && (
                                            <span
                                                className="move-quality-tag"
                                                style={{ color: qualityColor(pair.blackQuality.label) }}
                                                /* The full sentence, hedged as
                                                   far as the evidence allows -
                                                   the bare name asserted the
                                                   label with a confidence the
                                                   search does not always have.
                                                   See gradeSentence. */
                                                title={gradeSentence(pair.blackQuality, 'black')}
                                            >
                                                {pair.blackQuality.symbol}
                                            </span>
                                        )}
                                    </span>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};
