import React, { useState, useEffect, useCallback } from 'react';
import { Chessboard } from 'react-chessboard';
import { chessService } from '../services/chessService';
import type { GameState, ChessMove, LangflowConfig } from '../types/chess';
import type { Square } from 'chess.js';

interface ChessBoardProps {
    onGameStateChange?: (gameState: GameState) => void;
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

    useEffect(() => {
        const initializeGame = async () => {
            console.log('🎮 [GAME] Initializing chess game...');
            try {
                console.log('🔄 [API] Calling /api/reset...');
                const resetResponse = await fetch('/api/reset');
                const resetData = await resetResponse.json();
                console.log('✅ [API] Reset response:', resetData);
                
                if (resetData.success) {
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
                        setAiExplanation('');
                        console.log('🎉 [GAME] Game initialized successfully!');
                    }
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
        
        return () => window.removeEventListener('resize', updateBoardSize);
    }, []);

    useEffect(() => {
        onGameStateChange?.(gameState);
    }, [gameState, onGameStateChange]);

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

    const onSquareClick = useCallback((square: Square) => {
        if (gameState.turn !== 'white' || gameState.is_game_over || langflowConfig.status === 'thinking') {
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

        const piece = chessService.getPiece(square);
        
        if (piece && piece.color === 'w') {
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
    }, [gameState.turn, gameState.is_game_over, selectedSquare, possibleMoves, clearSelection, highlightSquares]);

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
                
                if (syncedGameState.turn !== 'white') {
                    console.log('⚠️ [MOVE] Not white\'s turn, aborting move');
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
    }, [updateGameState]);

    const startAiMovePolling = useCallback(() => {
        console.log('🔄 [AI] Starting AI move polling...');
        let pollCount = 0;
        const maxPolls = 30; // 30 seconds max
        
        const pollInterval = setInterval(async () => {
            pollCount++;
            console.log(`🔄 [AI] Polling attempt ${pollCount}/${maxPolls}`);
            
            try {
                const response = await fetch('/api/status');
                const data = await response.json();
                
                if (data.success && data.status) {
                    const currentTurn = data.status.turn;
                    console.log(`🎯 [AI] Current turn: ${currentTurn}`);
                    
                    // If it's white's turn, AI has completed its move
                    if (currentTurn === 'white') {
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
        
    }, [updateGameState]);

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
                    setAiExplanation('');
                    clearSelection();
                    
                    console.log('🎉 [RESET] Game reset completed successfully!');
                }
            } catch (error) {
                console.error('❌ [ERROR] Error syncing after reset:', error);
            }
        } else {
            console.log('❌ [RESET] Failed to reset game on server');
        }
    };

    const handleMakeAIMove = async () => {
        console.log('🤖 [AI] Manual AI move requested');
        console.log('📊 [STATE] Current game state:', { turn: gameState.turn, is_game_over: gameState.is_game_over });
        
        if (gameState.is_game_over) {
            console.log('⚠️ [AI] Cannot make AI move - game is over');
            return;
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

    return (
        <div className="chess-container">
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
                    arePiecesDraggable={false}
                />
            </div>
            
            {aiExplanation && (
                <div className="ai-explanation">
                    <h3>🤖 AI Analysis</h3>
                    <div className="explanation-content">
                        {aiExplanation}
                    </div>
                </div>
            )}
            
            <div className="game-controls">
                <div className="control-panel">
                    <div className="panel-header">
                        <h3>♟️ Chess Game Control</h3>
                        <div className="status-badges">
                            <span className={`turn-badge ${gameState.turn}`}>
                                Turn: {gameState.turn}
                            </span>
                            <span className="moves-badge">
                                Moves: {gameState.move_count}
                            </span>
                            <span className={`ai-status-badge ${langflowConfig.status}`}>
                                AI: {langflowConfig.status}
                            </span>
                        </div>
                    </div>

                    {(gameState.is_check || gameState.is_checkmate || gameState.is_stalemate) && (
                        <div className="game-alerts">
                            {gameState.is_check && <div className="alert check">CHECK!</div>}
                            {gameState.is_checkmate && <div className="alert checkmate">CHECKMATE!</div>}
                            {gameState.is_stalemate && <div className="alert stalemate">STALEMATE!</div>}
                        </div>
                    )}

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
                </div>
            </div>            
        </div>
    );
};