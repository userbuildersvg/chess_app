import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Chessboard } from 'react-chessboard';
import { chessService } from '../services/chessService';
import type { GameState, ChessMove, LangflowConfig } from '../types/chess';
import type { Square } from 'chess.js';
import { getCustomPieces, getBoardColors, PIECE_THEME_LIST } from '../pieceThemes';
import type { PieceThemeName } from '../pieceThemes';
interface ChessBoardProps {
    onGameStateChange?: (gameState: GameState) => void;
}
type PositionEval = {
    score: number | null;
    mate_in: number | null;
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
type HistoryEntry = {
    player: string;
    move: string;
    san: string;
    explanation?: string | null;
};
type MovePair = {
    moveNumber: number;
    white: string;
    black: string;
    isLastWhite: boolean;
    isLastBlack: boolean;
    blackExplanation: string | null;
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
            blackExplanation: blackEntry?.explanation ?? null
        });
    }
    return pairs;
};
type GameMode = 'human_vs_ai' | 'ai_vs_ai';
// Rail Canvas layout: Moves is always-visible next to the board (see
// .moves-panel in ChessBoard.css), the same treatment as the eval bar.
// Board Theme, Analysis, Learning and Chat all switch via the icon rail -
// Theme moved in here too so it doesn't need its own always-visible column.
type RailSectionId = 'theme' | 'analysis' | 'learning' | 'chat';
const RAIL_SECTIONS: { id: RailSectionId; icon: string; label: string }[] = [
    { id: 'analysis', icon: '🤖', label: 'Analysis' },
    { id: 'learning', icon: '🧠', label: 'Learning' },
    { id: 'chat', icon: '💬', label: 'Chat' },
    { id: 'theme', icon: '🎨', label: 'Board Theme' },
];
// Gemini's replies (chat + move analysis) occasionally use markdown - most
// commonly **bold** for emphasis ("that's **Fool's Mate**"). We render that
// as real bold instead of showing the literal asterisks, along with plain
// newlines, without pulling in a full markdown library for one formatting
// case. Anything that isn't a **...** pair (a stray "*", unmatched "**",
// etc.) just falls through unchanged as plain text.
function renderFormattedText(text: string): React.ReactNode {
    const segments = text.split(/(\*\*[^*]+\*\*)/g);
    return segments.map((segment, i) => {
        const boldMatch = segment.match(/^\*\*([^*]+)\*\*$/);
        const content = boldMatch ? boldMatch[1] : segment;
        const lines = content.split('\n').map((line, j) => (
            <React.Fragment key={j}>
                {j > 0 && <br />}
                {line}
            </React.Fragment>
        ));
        return boldMatch ? <strong key={i}>{lines}</strong> : <React.Fragment key={i}>{lines}</React.Fragment>;
    });
}
export const ChessBoard: React.FC<ChessBoardProps> = ({ onGameStateChange }) => {
    const [gameState, setGameState] = useState<GameState>(chessService.getGameState());
    const [langflowConfig, setLangflowConfig] = useState<LangflowConfig>({
        flowId: '8f2ac28a-4b1b-4bb0-8703-70e58cb00def',
        isConnected: false,
        status: 'idle'
    });
    const [selectedSquare, setSelectedSquare] = useState<Square | null>(null);
    const [possibleMoves, setPossibleMoves] = useState<string[]>([]);
    const [squareStyles, setSquareStyles] = useState<Record<string, React.CSSProperties>>({});
    const [boardSize, setBoardSize] = useState(440);
    const [aiExplanation, setAiExplanation] = useState<string>('');
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
    // Live mid-game chat with the AI (see /api/chat) - transcript is
    // per-game only, cleared server-side whenever a new game starts
    // (reset/color-switch/AI-vs-AI start), same as learning_service's
    // other per-game state.
    const [chatMessages, setChatMessages] = useState<{ role: 'user' | 'ai'; text: string }[]>([]);
    const [chatInput, setChatInput] = useState<string>('');
    const [chatSending, setChatSending] = useState<boolean>(false);
    // Live cross-game learning summary (see learning_service.py) - polled on
    // its own timer rather than wired into every move-completion path, so
    // the panel stays current without touching the already-intricate
    // polling logic used for AI moves and AI-vs-AI auto-play.
    const [learningSummary, setLearningSummary] = useState<any>(null);
    // Which rail section (Analysis/Learning/Chat) the canvas is showing,
    // plus a small unread dot for the other two when they get new content
    // while unfocused. Defaults to 'analysis', matching the pre-tab
    // behavior where the AI explanation was the thing shown by default.
    const [activeSection, setActiveSection] = useState<RailSectionId>('analysis');
    const [unreadSections, setUnreadSections] = useState<Record<RailSectionId, boolean>>({
        analysis: false, learning: false, chat: false, theme: false
    });
    // Bumped whenever aiExplanation/learningSummary actually change content.
    // Used as a React `key` on that section's canvas wrapper below so a
    // fresh value forces a remount - replaying the fade-slide-in CSS
    // animation even when the user is already looking at that section,
    // instead of the new text just popping into place.
    const [analysisUpdateKey, setAnalysisUpdateKey] = useState(0);
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
            const stored = localStorage.getItem('chess-piece-theme');
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
            localStorage.setItem('chess-piece-theme', pieceTheme);
        } catch {
            // localStorage unavailable - theme just won't persist this session
        }
    }, [pieceTheme]);
    const moveHistoryListRef = useRef<HTMLDivElement>(null);
    const aiVsAiPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
    useEffect(() => {
        const initializeGame = async () => {
            console.log('🎮 [GAME] Initializing chess game...');
            try {
                    console.log('🔄 [API] Calling /api/status...');
                    const response = await fetch('/api/status');
                    const data = await response.json();
                    console.log('✅ [API] Status response:', data);
                    if (data.success && data.status) {
                        console.log('♟️ [BOARD] Loading position:', data.status.fen);
                        chessService.loadPosition(data.status.fen);
                        const newGameState = chessService.getGameState();
                        console.log('📊 [STATE] New game state:', newGameState);
                        setGameState(newGameState);
                        setMoveCount(data.status.move_count);
                        setAiExplanation('');
                        if (typeof data.difficulty === 'number') {
                            console.log('🎚️ [DIFFICULTY] Synced from server:', data.difficulty);
                            setDifficulty(data.difficulty);
                        }
                        if (data.eval) {
                            setBoardEval(data.eval);
                        }
                        setMoveHistory(data.history || []);
                        // Restore the chat transcript on load/refresh - the
                        // backend already kept it (chat_history is only
                        // cleared server-side when a new game actually
                        // starts), it just was never sent down before now.
                        if (Array.isArray(data.chat_history)) {
                            setChatMessages(data.chat_history.map((turn: { role: string; text: string }) => ({
                                role: turn.role === 'model' ? 'ai' : 'user',
                                text: turn.text
                            })));
                        }
                        if (data.player_color === 'white' || data.player_color === 'black') {
                            setPlayerColorState(data.player_color);
                        }
                        if (data.game_mode === 'ai_vs_ai') {
                            console.log('🤖⚔️🤖 [AIVAI] Resuming AI vs AI mode after reload');
                            setGameMode('ai_vs_ai');
                            setAiVsAiRunning(!!data.ai_vs_ai_running);
                            if (data.ai_vs_ai_running) {
                                startAiVsAiPolling();
                            }
                        }
                        console.log('🎉 [GAME] Game initialized successfully!');
                }
            } catch (error) {
                console.error('❌ [ERROR] Error initializing game:', error);
            }
        };
        initializeGame();
        const updateBoardSize = () => {
            const width = window.innerWidth;
            if (width < 480) {
                setBoardSize(Math.min(440, width - 100));
            } else {
                setBoardSize(440);
            }
        };
        updateBoardSize();
        window.addEventListener('resize', updateBoardSize);
        return () => {
            window.removeEventListener('resize', updateBoardSize);
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
            const response = await fetch('/api/learning/summary');
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
    const activeSectionRef = useRef<RailSectionId>('analysis');
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
    // Replay the Analysis section's fade-in and flag it unread whenever a
    // fresh explanation lands.
    useEffect(() => {
        if (!aiExplanation) {
            return;
        }
        setAnalysisUpdateKey(k => k + 1);
        setActiveTabAwareUnread('analysis');
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [aiExplanation]);
    // learningSummary is re-fetched on a 5s timer whether or not anything
    // actually changed - compare the serialized payload first so polling
    // alone doesn't replay the animation or flag "unread" every 5 seconds
    // for no reason.
    useEffect(() => {
        if (!learningSummary) {
            return;
        }
        const json = JSON.stringify(learningSummary);
        if (json === prevLearningJsonRef.current) {
            return;
        }
        prevLearningJsonRef.current = json;
        setLearningUpdateKey(k => k + 1);
        setActiveTabAwareUnread('learning');
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [learningSummary]);
    // Flag Chat unread only when a new AI reply lands - not on the user's
    // own outgoing message, which they obviously already saw.
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
        setPossibleMoves([]);
        setSquareStyles({});
    }, []);
    const updateGameState = useCallback(() => {
        const newGameState = chessService.getGameState();
        setGameState(newGameState);
        clearSelection();
    }, [clearSelection]);
    const highlightSquares = useCallback((squares: { [square: string]: React.CSSProperties }) => {
        setSquareStyles(squares);
    }, []);
    const refreshEval = useCallback(async () => {
        try {
            const response = await fetch('/api/status');
            const data = await response.json();
            if (data.success && data.eval) {
                setBoardEval(data.eval);
            }
            if (data.success && data.history) {
                setMoveHistory(data.history);
            }
        } catch (error) {
            console.error('❌ [EVAL] Failed to refresh evaluation:', error);
        }
    }, []);
    const onSquareClick = useCallback(async (square: Square) => {
        if (gameMode === 'ai_vs_ai') {
            return;
        }
        if (gameState.turn !== playerColor || gameState.is_game_over || langflowConfig.status === 'thinking') {
            return;
        }
        if (selectedSquare === square) {
            clearSelection();
            return;
        }
        if (selectedSquare && possibleMoves.includes(square)) {
            makePlayerMove(selectedSquare, square);
            return;
        }
        try {
            const statusResponse = await fetch('/api/status');
            const statusData = await statusResponse.json();
            if (statusData.success && statusData.status) {
                chessService.loadPosition(statusData.status.fen);
                setMoveCount(statusData.status.move_count);
            }
        } catch (error) {
            console.error('Failed to sync before square click:', error);
        }
        const piece = chessService.getPiece(square);
        const playerPieceColor = playerColor === 'white' ? 'w' : 'b';
        if (piece && piece.color === playerPieceColor) {
            setSelectedSquare(square);
            const moves = chessService.getLegalMoves(square);
            setPossibleMoves(moves);
            const styles: Record<string, React.CSSProperties> = {
                [square]: { backgroundColor: '#fbbf24', opacity: 0.8 }
            };
            moves.forEach(moveSquare => {
                const targetPiece = chessService.getPiece(moveSquare);
                styles[moveSquare] = {
                    backgroundColor: targetPiece ? '#ef4444' : '#10b981',
                    opacity: 0.8
                };
            });
            highlightSquares(styles);
        } else {
            clearSelection();
        }
    }, [gameMode, gameState.turn, gameState.is_game_over, playerColor, selectedSquare, possibleMoves, clearSelection, highlightSquares]);
    const makePlayerMove = useCallback(async (from: Square, to: Square) => {
        console.log(`🎯 [MOVE] Player attempting move: ${from} → ${to}`);
        try {
            console.log('🔄 [API] Syncing with server before move...');
            const statusResponse = await fetch('/api/status');
            const statusData = await statusResponse.json();
            console.log('📊 [SYNC] Server status:', statusData);
            if (statusData.success && statusData.status) {
                console.log('♟️ [BOARD] Loading server position:', statusData.status.fen);
                chessService.loadPosition(statusData.status.fen);
                const syncedGameState = chessService.getGameState();
                console.log('📊 [STATE] Synced game state:', syncedGameState);
                setGameState(syncedGameState);
                setMoveCount(statusData.status.move_count);
                if (syncedGameState.turn !== playerColor) {
                    console.log('⚠️ [MOVE] Not your turn, aborting move');
                    return;
                }
            }
        } catch (error) {
            console.error('❌ [ERROR] Failed to sync with server:', error);
            return;
        }
        // Check if this is a pawn promotion
        const piece = chessService.getPiece(from);
        const isPromotion = piece && piece.type === 'p' && (to[1] === '8' || to[1] === '1');
        const move: ChessMove = {
            from,
            to,
            promotion: isPromotion ? 'q' : undefined
        };
        console.log('🎯 [MOVE] Executing player move:', move);
        const result = await chessService.makePlayerMove(move);
        console.log('✅ [MOVE] Player move result:', result);
        if (result.success) {
            console.log('🔄 [STATE] Updating game state after player move...');
            updateGameState();
            setMoveCount(result.status?.move_count ?? moveCount + 1);
            refreshEval();
            if (result.model_move) {
                console.log('🤖 [AI] AI move info:', result.model_move);
                if (result.model_move.ai_scheduled) {
                    console.log('🎯 [AI] AI move scheduled in background - starting polling');
                    setLangflowConfig(prev => ({ ...prev, status: 'thinking' }));
                    setAiExplanation('AI is thinking...');
                    startAiMovePolling();
                } else if (result.model_move.manual_ai_required) {
                    console.log('🎯 [AI] Manual AI move required - use Make AI Move button');
                    setLangflowConfig(prev => ({ ...prev, status: 'idle' }));
                    setAiExplanation('Use "Make AI Move" button to play AI move');
                } else if (result.model_move.success) {
                    const explanation = result.model_move.explanation || result.model_move.message || 'AI move completed';
                    console.log('✅ [AI] AI move completed with explanation:', explanation);
                    setAiExplanation(explanation);
                    if (result.model_move.move && result.model_move.san) {
                        console.log('📝 [AI] AI move completed:', result.model_move.san);
                    }
                } else {
                    console.log('ℹ️ [AI] No AI move needed:', result.model_move.message);
                    setLangflowConfig(prev => ({ ...prev, status: 'idle' }));
                }
            } else {
                console.log('ℹ️ [AI] No AI move info in response');
            }
        } else {
            console.log('❌ [MOVE] Player move failed:', result);
        }
    }, [updateGameState, refreshEval, playerColor]);
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
        console.log(`🔄 [AI] Starting AI move polling (waiting for ${targetColor}'s turn)...`);
        let pollCount = 0;
        // Gemini/Langflow calls have been observed taking 20-25+ seconds on
        // their own, before Stockfish ranking and network overhead - 30
        // consecutive 1s polls (30s total) was cutting it too close and
        // would occasionally time out the UI a moment before the backend
        // actually finished the move, leaving the frontend stuck showing a
        // stale "your turn" state. 90s gives enough headroom.
        const maxPolls = 90; // 90 seconds max
        const pollInterval = setInterval(async () => {
            pollCount++;
            console.log(`🔄 [AI] Polling attempt ${pollCount}/${maxPolls}`);
            try {
                const response = await fetch('/api/status');
                const data = await response.json();
                if (data.success && data.status) {
                    const currentTurn = data.status.turn;
                    console.log(`🎯 [AI] Current turn: ${currentTurn}`);
                    // AI move is done once it's the target color's turn again.
                    if (currentTurn === targetColor) {
                        console.log('✅ [AI] AI move completed - updating game state');
                        clearInterval(pollInterval);
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
                        console.log('📊 [STATE] AI completed - new game state:', newGameState);
                        // CRITICAL: Update chessService with new position
                        console.log('♟️ [BOARD] Loading AI completed position into chessService:', newGameState.fen);
                        chessService.loadPosition(newGameState.fen);
                        setGameState(newGameState);
                        setMoveCount(newGameState.move_count);
                        if (data.eval) {
                            setBoardEval(data.eval);
                        }
                        if (data.history) {
                            setMoveHistory(data.history);
                        }
                        // Update move history if available
                        if (data.history && data.history.length > 0) {
                            const lastMove = data.history[data.history.length - 1];
                            if (lastMove.player === 'langflow') {
                                console.log('📝 [AI] AI move from history:', lastMove.san);
                                if (lastMove.explanation) {
                                    console.log('💭 [AI] Setting AI explanation:', lastMove.explanation);
                                    setAiExplanation(lastMove.explanation);
                                } else {
                                    setAiExplanation('AI move completed');
                                }
                            }
                        } else {
                            setAiExplanation('AI move completed');
                        }
                        setLangflowConfig(prev => ({ ...prev, status: 'connected' }));
                        return;
                    }
                }
                // Stop polling after max attempts
                if (pollCount >= maxPolls) {
                    console.log('⏰ [AI] Polling timeout - stopping');
                    clearInterval(pollInterval);
                    setLangflowConfig(prev => ({ ...prev, status: 'error' }));
                    setAiExplanation('AI move timeout - please try manual AI move');
                }
            } catch (error) {
                console.error('❌ [AI] Polling error:', error);
                pollCount = maxPolls; // Stop on error
            }
        }, 1000); // Poll every second
    }, [playerColor]);
    // Ongoing poll for AI vs AI auto-play - unlike startAiMovePolling (which
    // polls until exactly one move lands), this keeps polling for as long
    // as the backend reports ai_vs_ai_running, since moves keep happening
    // on their own with no user action in between. Stops itself once the
    // game ends, mode is exited, or auto-play is paused.
    const startAiVsAiPolling = useCallback(() => {
        console.log('🔄 [AIVAI] Starting AI vs AI polling...');
        if (aiVsAiPollRef.current) {
            clearInterval(aiVsAiPollRef.current);
        }
        let lastMoveCount = -1;
        const pollInterval = setInterval(async () => {
            try {
                const response = await fetch('/api/status');
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
                        const lastMove = data.history[data.history.length - 1];
                        if (lastMove?.explanation) {
                            setAiExplanation(lastMove.explanation);
                        }
                    }
                }

                setAiVsAiRunning(!!data.ai_vs_ai_running);

                if (data.game_mode !== 'ai_vs_ai' || data.status.is_game_over || !data.ai_vs_ai_running) {
                    console.log('🏁 [AIVAI] Stopping poll (mode exited, game over, or paused)');
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
        console.log('🔄 [RESET] Game reset requested');
        const success = await chessService.resetGameOnServer();
        console.log('📊 [RESET] Reset server response:', success);
        if (success) {
            try {
                console.log('🔄 [API] Fetching status after reset...');
                const response = await fetch('/api/status');
                const data = await response.json();
                console.log('✅ [API] Status after reset:', data);
                if (data.success && data.status) {
                    console.log('♟️ [BOARD] Loading reset position:', data.status.fen);
                    chessService.loadPosition(data.status.fen);
                    const newGameState = chessService.getGameState();
                    console.log('📊 [STATE] New game state after reset:', newGameState);
                    setGameState(newGameState);
                    setMoveCount(0);
                    setAiExplanation('');
                    clearSelection();
                    if (data.eval) {
                        setBoardEval(data.eval);
                    }
                    setMoveHistory(data.history || []);
                    // Server-side chat_history is cleared on every reset -
                    // mirror that here instead of leaving stale messages
                    // from the previous game sitting in the chat tab.
                    setChatMessages([]);
                    setGameMode('human_vs_ai');
                    setAiVsAiRunning(false);
                    console.log('🎉 [RESET] Game reset completed successfully!');
                }
            } catch (error) {
                console.error('❌ [ERROR] Error syncing after reset:', error);
            }
        } else {
            console.log('❌ [RESET] Failed to reset game on server');
        }
    };
    const handleSetColor = async (color: 'white' | 'black') => {
        console.log(`🎨 [COLOR] Switching to play as ${color}`);
        setLangflowConfig(prev => ({ ...prev, status: 'idle' }));
        setAiExplanation('');
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
            if (typeof result.difficulty === 'number') {
                setDifficulty(result.difficulty);
            }
            if (result.ai_scheduled) {
                console.log('🎯 [COLOR] AI moves first - starting polling');
                setLangflowConfig(prev => ({ ...prev, status: 'thinking' }));
                setAiExplanation('AI is thinking...');
                // Pass `color` explicitly rather than letting the poller
                // default to the `playerColor` closure - see the comment
                // on startAiMovePolling for why that closure is stale here.
                startAiMovePolling(color);
            }
            console.log(`✅ [COLOR] Now playing as ${color}`);
        } else {
            console.error('❌ [COLOR] Failed to switch color:', result);
        }
    };
    const handleStartAiVsAi = async () => {
        console.log('🎬 [AIVAI] Starting AI vs AI');
        setAiExplanation('');
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
            setLangflowConfig(prev => ({ ...prev, status: 'connected' }));
            startAiVsAiPolling();
        } else {
            console.error('❌ [AIVAI] Failed to start AI vs AI:', result);
        }
    };
    const handlePauseAiVsAi = async () => {
        console.log('⏸️ [AIVAI] Pausing');
        const result = await chessService.pauseAiVsAi();
        if (result.success) {
            setAiVsAiRunning(false);
        } else {
            console.error('❌ [AIVAI] Failed to pause:', result);
        }
    };
    const handleResumeAiVsAi = async () => {
        console.log('▶️ [AIVAI] Resuming');
        const result = await chessService.resumeAiVsAi();
        if (result.success) {
            setAiVsAiRunning(true);
            startAiVsAiPolling();
        } else {
            console.error('❌ [AIVAI] Failed to resume:', result);
        }
    };
    const handleStepAiVsAi = async () => {
        console.log('⏭️ [AIVAI] Stepping one move');
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
                const lastMove = result.history[result.history.length - 1];
                if (lastMove?.explanation) {
                    setAiExplanation(lastMove.explanation);
                }
            }
        } else {
            console.error('❌ [AIVAI] Failed to step:', result);
        }
    };
    const handleExitAiVsAi = async () => {
        console.log('✖️ [AIVAI] Exiting AI vs AI mode');
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
                const statusResponse = await fetch('/api/status');
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
        console.log('🤖 [AI] Manual AI move requested');
        console.log('📊 [STATE] Current game state:', { turn: gameState.turn, is_game_over: gameState.is_game_over });
        if (gameMode === 'ai_vs_ai') {
            console.log('⚠️ [AI] Not applicable in AI vs AI mode');
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
            const statusResponse = await fetch('/api/status');
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
                    console.log(`ℹ️ [AI] Server already advanced past ${aiColor}'s turn - nothing to do`);
                    setLangflowConfig(prev => ({ ...prev, status: 'idle' }));
                    return;
                }
                if (syncedGameState.is_game_over) {
                    console.log('⚠️ [AI] Cannot make AI move - game is over');
                    return;
                }
            }
        } catch (error) {
            console.error('❌ [ERROR] Failed to sync before AI move:', error);
        }
        console.log('⏳ [AI] Setting thinking status and clearing explanation...');
        setLangflowConfig(prev => ({ ...prev, status: 'thinking' }));
        setAiExplanation('');
        try {
            console.log('🔄 [API] Calling /api/ai-move...');
            const response = await fetch('/api/ai-move', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                }
            });
            const result = await response.json();
            console.log('✅ [API] AI move response:', result);
            if (result.success) {
                console.log('🎉 [AI] AI move successful!');
                // Update game state
                const newGameState = result.game_state;
                console.log('📊 [STATE] New game state from AI move:', newGameState);
                setGameState(newGameState);
                setMoveCount(newGameState.move_count);
                if (result.eval) {
                    setBoardEval(result.eval);
                }
                if (result.history) {
                    setMoveHistory(result.history);
                }
                // Show AI reasoning
                if (result.reasoning) {
                    console.log('💭 [AI] AI reasoning:', result.reasoning);
                    setAiExplanation(result.reasoning);
                } else {
                    console.log('ℹ️ [AI] No reasoning provided');
                }
                console.log('✅ [AI] Setting connected status');
                setLangflowConfig(prev => ({ ...prev, status: 'connected' }));
            } else {
                console.log('❌ [AI] AI move failed:', result);
                // Show detailed error information
                const errorMsg = result.message || 'Unknown error';
                const errorDetails = result.error_type?.error || '';
                const reasoning = result.error_type?.reasoning || '';
                console.log('🔍 [ERROR] Error details:', { errorMsg, errorDetails, reasoning });
                let fullErrorMsg = `❌ ${errorMsg}`;
                if (errorDetails) {
                    fullErrorMsg += `\n\n🔍 Details: ${errorDetails}`;
                }
                if (reasoning) {
                    fullErrorMsg += `\n\n🤖 AI Response: ${reasoning}`;
                }
                console.log('📝 [ERROR] Full error message:', fullErrorMsg);
                setAiExplanation(fullErrorMsg);
                setLangflowConfig(prev => ({ ...prev, status: 'error' }));
            }
        } catch (error) {
            console.error('❌ [ERROR] Network error making AI move:', error);
            const errorMessage = error instanceof Error ? error.message : 'Unknown error';
            const networkError = `❌ Network error: ${errorMessage}`;
            console.log('📝 [ERROR] Network error message:', networkError);
            setAiExplanation(networkError);
            setLangflowConfig(prev => ({ ...prev, status: 'error' }));
        }
    };
    const handleDifficultyChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
        const newDifficulty = parseInt(event.target.value, 10);
        console.log(`🎚️ [DIFFICULTY] Slider moved to ${newDifficulty}`);
        setDifficulty(newDifficulty); // optimistic update so the slider feels responsive
        try {
            const response = await fetch('/api/difficulty', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ difficulty: newDifficulty })
            });
            const result = await response.json();
            if (result.success) {
                console.log(`✅ [DIFFICULTY] Server confirmed difficulty=${result.difficulty}`);
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
    }, [chatMessages, chatSending]);
    const handleSendChatMessage = async (event: React.FormEvent) => {
        event.preventDefault();
        const message = chatInput.trim();
        if (!message || chatSending) {
            return;
        }
        setChatMessages(prev => [...prev, { role: 'user', text: message }]);
        setChatInput('');
        setChatSending(true);
        try {
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message })
            });
            const data = await response.json();
            if (data.success) {
                const reply = data.reply ?? data.data?.reply ?? '(no reply)';
                setChatMessages(prev => [...prev, { role: 'ai', text: reply }]);
            } else {
                const errorText = data.error_type?.message || data.error_type?.error || data.message || 'Chat request failed';
                setChatMessages(prev => [...prev, { role: 'ai', text: `⚠️ ${errorText}` }]);
            }
        } catch (error) {
            console.error('❌ [CHAT] Network error sending chat message:', error);
            setChatMessages(prev => [...prev, { role: 'ai', text: '⚠️ Network error - could not reach the AI.' }]);
        } finally {
            setChatSending(false);
        }
    };
    const movePairs = buildMovePairs(moveHistory);
    const customPieces = getCustomPieces(pieceTheme);
    const boardColors = getBoardColors(pieceTheme);
    return (
        <div className="chess-container">
            <div className="board-area">
                <div className="board-column">
                    <div className="board-row">
                        <div className="eval-bar-wrapper">
                            <div className="eval-bar" style={{ height: boardSize }} title="Position evaluation (White's perspective)">
                                <div
                                    className="eval-bar-fill"
                                    style={{ height: `${evalToWhitePercent(boardEval)}%` }}
                                />
                            </div>
                            <span className="eval-bar-label">{formatEval(boardEval)}</span>
                        </div>
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
                                boardOrientation={playerColor}
                                arePiecesDraggable={false}
                                // Pieces (ours and the AI's) now slide to their new
                                // square instead of snapping - react-chessboard
                                // animates any position-prop change on its own as
                                // long as this is > 0, including AI moves that
                                // arrive via polling rather than a drag.
                                animationDuration={300}
                                customPieces={customPieces}
                                customDarkSquareStyle={boardColors ? { backgroundColor: boardColors.dark } : undefined}
                                customLightSquareStyle={boardColors ? { backgroundColor: boardColors.light } : undefined}
                            />
                        </div>
                    </div>
                <div className="game-controls">
                <div className="control-panel">
                    <div className="panel-header">
                        <h3>♟️ Chess Game Control</h3>
                        <div className="status-badges">
                            <span className={`turn-badge ${gameState.turn}`}>
                                Turn: {gameState.turn}
                            </span>
                            <span className="moves-badge">
                                Moves: {moveCount}
                            </span>
                            <span className={`ai-status-badge ${langflowConfig.status}`}>
                                AI: {langflowConfig.status}
                            </span>
                        </div>
                    </div>
                    <div className="difficulty-control">
                        <div className="difficulty-header">
                            <span className="difficulty-label">🎚️ AI Difficulty</span>
                            <span className="difficulty-value">{difficulty}/20</span>
                        </div>
                        <input
                            id="difficulty-slider"
                            type="range"
                            min={1}
                            max={20}
                            step={1}
                            value={difficulty}
                            onChange={handleDifficultyChange}
                            className="difficulty-slider"
                            aria-label="AI difficulty"
                        />
                        <div className="difficulty-labels">
                            <span>Weakest</span>
                            <span>Strongest</span>
                        </div>
                    </div>
                    {(gameState.is_check || gameState.is_checkmate || gameState.is_stalemate) && (
                        <div className="game-alerts">
                            {gameState.is_check && <div className="alert check">CHECK!</div>}
                            {gameState.is_checkmate && <div className="alert checkmate">CHECKMATE!</div>}
                            {gameState.is_stalemate && <div className="alert stalemate">STALEMATE!</div>}
                        </div>
                    )}
                    {gameMode === 'human_vs_ai' ? (
                        <>
                            <div className="action-buttons">
                                <button
                                    onClick={handleReset}
                                    className="action-btn reset-btn"
                                >
                                    🔄 Reset Game
                                </button>
                                {!gameState.is_game_over && (
                                    <button
                                        onClick={handleMakeAIMove}
                                        className="action-btn ai-move-btn"
                                        disabled={langflowConfig.status === 'thinking'}
                                    >
                                        {langflowConfig.status === 'thinking' ? '🤖 AI Thinking...' : '🤖 Make AI Move'}
                                    </button>
                                )}
                            </div>
                            <div className="mode-buttons">
                                <button
                                    onClick={() => handleSetColor(playerColor === 'white' ? 'black' : 'white')}
                                    className="action-btn color-switch-btn"
                                    disabled={langflowConfig.status === 'thinking'}
                                >
                                    🔁 Play as {playerColor === 'white' ? 'Black' : 'White'}
                                </button>
                                <button
                                    onClick={handleStartAiVsAi}
                                    className="action-btn ai-vs-ai-btn"
                                    disabled={langflowConfig.status === 'thinking'}
                                >
                                    🤖⚔️🤖 AI vs AI
                                </button>
                            </div>
                        </>
                    ) : (
                        <div className="action-buttons ai-vs-ai-controls">
                            {aiVsAiRunning ? (
                                <button onClick={handlePauseAiVsAi} className="action-btn pause-btn">
                                    ⏸️ Pause
                                </button>
                            ) : (
                                <>
                                    <button
                                        onClick={handleResumeAiVsAi}
                                        className="action-btn resume-btn"
                                        disabled={gameState.is_game_over}
                                    >
                                        ▶️ Resume
                                    </button>
                                    <button
                                        onClick={handleStepAiVsAi}
                                        className="action-btn step-btn"
                                        disabled={gameState.is_game_over}
                                    >
                                        ⏭️ Next Move
                                    </button>
                                </>
                            )}
                            <button onClick={handleExitAiVsAi} className="action-btn exit-btn">
                                ✖️ Exit AI vs AI
                            </button>
                        </div>
                    )}
                </div>
                </div>
                </div>
                <div className="ai-column">
                <div className="rail-canvas-panel">
                    <div className="rail-canvas-content">
                        {activeSection === 'analysis' && (
                            <div className="rail-canvas-inner fade-slide-in" key={`analysis-panel-${analysisUpdateKey}`}>
                                {aiExplanation ? (
                                    <div className="explanation-content">{renderFormattedText(aiExplanation)}</div>
                                ) : (
                                    <div className="move-history-empty">No AI analysis yet - make a move or ask the AI to move.</div>
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
                                    <div className="move-history-empty">Loading learning data...</div>
                                )}
                            </div>
                        )}
                        {activeSection === 'chat' && (
                            <div className="rail-canvas-inner chat-canvas-inner fade-slide-in" key="chat-panel">
                                <div className="chat-messages" ref={chatListRef}>
                                    {chatMessages.length === 0 && (
                                        <div className="chat-empty">Ask about the position, its plans, or what it expects you to play.</div>
                                    )}
                                    {chatMessages.map((msg, i) => (
                                        <div key={i} className={`chat-message chat-message-${msg.role}`}>
                                            {renderFormattedText(msg.text)}
                                        </div>
                                    ))}
                                    {chatSending && (
                                        <div className="chat-message chat-message-ai chat-message-pending">
                                            <span className="thinking-dots mini" aria-label="AI is typing">
                                                <span></span><span></span><span></span>
                                            </span>
                                        </div>
                                    )}
                                </div>
                                <form className="chat-input-row" onSubmit={handleSendChatMessage}>
                                    <input
                                        type="text"
                                        value={chatInput}
                                        onChange={(e: React.ChangeEvent<HTMLInputElement>) => setChatInput(e.target.value)}
                                        placeholder="Ask the AI something..."
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
                    <div className="rail-stack" role="tablist">
                        {RAIL_SECTIONS.map(section => (
                            <button
                                key={section.id}
                                type="button"
                                role="tab"
                                aria-selected={activeSection === section.id}
                                aria-label={section.label}
                                title={section.label}
                                className={`rail-icon-btn ${activeSection === section.id ? 'active' : ''}`}
                                onClick={() => handleSectionClick(section.id)}
                            >
                                <span className="rail-icon" aria-hidden="true">{section.icon}</span>
                                {section.id === 'analysis' && langflowConfig.status === 'thinking' && (
                                    <span className="rail-thinking-dot" aria-hidden="true" />
                                )}
                                {unreadSections[section.id] && activeSection !== section.id && (
                                    <span className="rail-unread-dot" aria-hidden="true" />
                                )}
                            </button>
                        ))}
                    </div>
                </div>
                    <div className="moves-panel">
                        <h3 className="moves-panel-title">📜 Moves</h3>
                        <div className="move-history-list" ref={moveHistoryListRef}>
                            {movePairs.length === 0 && (
                                <div className="move-history-empty">No moves yet</div>
                            )}
                            {movePairs.map(pair => (
                                <div key={pair.moveNumber} className="move-history-row">
                                    <span className="move-history-number">{pair.moveNumber}.</span>
                                    <span className={`move-history-white ${pair.isLastWhite ? 'move-history-current' : ''}`}>
                                        {pair.white}
                                    </span>
                                    <span
                                        className={`move-history-black ${pair.isLastBlack ? 'move-history-current' : ''}`}
                                        title={pair.blackExplanation || undefined}
                                    >
                                        {pair.black}
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
