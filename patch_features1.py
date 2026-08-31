#!/usr/bin/env python3
"""
CSS additions for the color-switch / AI-vs-AI feature round:
  1. Move-history white/black pills get nowrap + ellipsis (+ min-width: 0,
     required for ellipsis to actually work inside a CSS grid column) as a
     defensive hardening against any unusually long SAN in a late-game
     move history.
  2. New button styles for the "Play as Black/White" toggle, the "AI vs
     AI" entry button, and the Pause/Resume/Next Move/Exit AI-vs-AI
     controls that replace the normal action buttons while that mode is
     active.

Run this from the repo root:
    python3 patch_features1.py chess-frontend/src/components/ChessBoard.css
"""
import sys


def patch_file(path, replacements):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    for old, new, label in replacements:
        count = src.count(old)
        if count != 1:
            print(f"❌ {path}: expected exactly 1 match for '{label}', found {count}. Aborting - no changes written.")
            sys.exit(1)
        src = src.replace(old, new, 1)

    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"✅ {path}: applied {len(replacements)} patch(es)")


if len(sys.argv) != 2:
    print("Usage: python3 patch_features1.py <path to ChessBoard.css>")
    sys.exit(1)

css_replacements = [
    (
'''.move-history-white {
    background: linear-gradient(135deg, #f1f5f9 0%, #e2e8f0 100%);
    color: #334155;
    border-radius: 4px;
    padding: 2px 6px;
    font-weight: 600;
    text-align: center;
}

.move-history-black {
    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
    color: #f1f5f9;
    border: 1px solid rgba(71, 85, 105, 0.4);
    border-radius: 4px;
    padding: 2px 6px;
    font-weight: 600;
    text-align: center;
}''',
'''.move-history-white {
    background: linear-gradient(135deg, #f1f5f9 0%, #e2e8f0 100%);
    color: #334155;
    border-radius: 4px;
    padding: 2px 6px;
    font-weight: 600;
    text-align: center;
    min-width: 0;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
}

.move-history-black {
    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
    color: #f1f5f9;
    border: 1px solid rgba(71, 85, 105, 0.4);
    border-radius: 4px;
    padding: 2px 6px;
    font-weight: 600;
    text-align: center;
    min-width: 0;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
}''',
        "harden move-history pills against long SAN (nowrap + ellipsis)",
    ),
    (
'''    .move-history-list {
        max-height: 320px;
    }
}''',
'''    .move-history-list {
        max-height: 320px;
    }
}

/* ===== Mode controls (color switch / AI vs AI) ===== */
.mode-buttons {
    display: flex;
    gap: 16px;
    justify-content: center;
    margin-top: 4px;
}
.color-switch-btn {
    background: linear-gradient(135deg, #7c3aed 0%, #6d28d9 100%);
    color: white;
    border: 1px solid #7c3aed;
}
.color-switch-btn:hover:not(:disabled) {
    background: linear-gradient(135deg, #6d28d9 0%, #5b21b6 100%);
    transform: translateY(-2px);
    box-shadow: 0 8px 12px -2px rgba(124, 58, 237, 0.4);
}
.color-switch-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
    transform: none;
}
.ai-vs-ai-btn {
    background: linear-gradient(135deg, #0891b2 0%, #0e7490 100%);
    color: white;
    border: 1px solid #0891b2;
}
.ai-vs-ai-btn:hover:not(:disabled) {
    background: linear-gradient(135deg, #0e7490 0%, #155e75 100%);
    transform: translateY(-2px);
    box-shadow: 0 8px 12px -2px rgba(8, 145, 178, 0.4);
}
.ai-vs-ai-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
    transform: none;
}
.pause-btn {
    background: linear-gradient(135deg, #d97706 0%, #b45309 100%);
    color: white;
    border: 1px solid #d97706;
}
.pause-btn:hover {
    background: linear-gradient(135deg, #b45309 0%, #92400e 100%);
    transform: translateY(-2px);
    box-shadow: 0 8px 12px -2px rgba(217, 119, 6, 0.4);
}
.resume-btn {
    background: linear-gradient(135deg, #059669 0%, #047857 100%);
    color: white;
    border: 1px solid #059669;
}
.resume-btn:hover:not(:disabled) {
    background: linear-gradient(135deg, #047857 0%, #065f46 100%);
    transform: translateY(-2px);
    box-shadow: 0 8px 12px -2px rgba(5, 150, 105, 0.4);
}
.resume-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
    transform: none;
}
.step-btn {
    background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
    color: white;
    border: 1px solid #2563eb;
}
.step-btn:hover:not(:disabled) {
    background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
    transform: translateY(-2px);
    box-shadow: 0 8px 12px -2px rgba(59, 130, 246, 0.4);
}
.step-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
    transform: none;
}
.exit-btn {
    background: linear-gradient(135deg, #991b1b 0%, #7f1d1d 100%);
    color: white;
    border: 1px solid #991b1b;
}
.exit-btn:hover {
    background: linear-gradient(135deg, #7f1d1d 0%, #601616 100%);
    transform: translateY(-2px);
    box-shadow: 0 8px 12px -2px rgba(153, 27, 27, 0.4);
}
@media (max-width: 480px) {
    .mode-buttons {
        flex-direction: column;
        gap: 10px;
    }
}''',
        "append mode-control button styles",
    ),
]

patch_file(sys.argv[1], css_replacements)
print("🎉 All patches applied successfully.")
