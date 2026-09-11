import type { BoardSizePref } from '../hooks/useBoardScale';
import { BOARD_SIZE_OPTIONS } from '../hooks/useBoardScale';
import type { FC } from 'react';

/** Shared board-size selector for Play, Learn, and Review. */
export const BoardSizeControl: FC<{
    value: BoardSizePref;
    onChange: (next: BoardSizePref) => void;
}> = ({ value, onChange }) => (
    <label className="ws-board-size">
        <span className="ws-label-full">Board</span>
        <select
            aria-label="Board size"
            value={value}
            onChange={event => onChange(event.target.value as BoardSizePref)}
            title="How large the board is drawn. Auto follows the window."
        >
            {BOARD_SIZE_OPTIONS.map(option => (
                <option key={option.id} value={option.id}>
                    {option.label}
                </option>
            ))}
        </select>
    </label>
);
