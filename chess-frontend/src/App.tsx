import { useState } from 'react';
import { ChessBoard } from './components/ChessBoard';
import type { GameState } from './types/chess';
import './components/ChessBoard.css';
import './App.css';
function App() {
    const [, setGameState] = useState<GameState | null>(null);
    return (
        <div className="app">
            <header className="app-header">
                <h1>♟️ Zugzwang</h1>
            </header>
            <main className="app-main">
                <ChessBoard onGameStateChange={setGameState} />
            </main>
        </div>
    );
}
export default App;
