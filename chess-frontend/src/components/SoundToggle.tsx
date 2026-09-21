import { useSoundPref } from '../sound';

/** The one sound switch, in each mode's Actions tab. Same row as the other switches. */
export function SoundToggle() {
    const [on, setOn] = useSoundPref();
    return (
        <label className="game-switch actions-item">
            <input type="checkbox" checked={on} onChange={() => setOn(!on)} data-testid="sound-toggle" />
            <span>Sound</span>
            <span className="actions-note">A quiet click on each move, a chime on check and at the end of the game.</span>
        </label>
    );
}
