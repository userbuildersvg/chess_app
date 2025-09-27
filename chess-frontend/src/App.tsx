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
                <h1>♟️ Chess with Langflow AI</h1>
                <p>Play chess against AI powered by Langflow</p>
            </header>
            
            <main className="app-main">
                <ChessBoard onGameStateChange={setGameState} />
            </main>
            
            <footer className="app-footer">
                <p>Built with React, TypeScript, Vite & Langflow</p>
            </footer>
        </div>
    );
}

export default App;
