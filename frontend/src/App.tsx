import React from 'react'

interface Props { children: React.ReactNode }

/**
 * App shell (no top navigation per spec).
 * Keeps the canvas locked and centered, supplies liquid-glass background.
 */
export default function App({ children }: Props) {
  return (
    <div className="app-shell">
      <div className="bg-base" />
      {/* dynamic color wash is added by Dashboard via tone-* classes */}
      {children}
    </div>
  )
}
