import React from 'react'

/**
 * Learning Page
 *
 * This page is reserved for advanced features that allow the trading bot to
 * fine‑tune itself. In a later phase we might expose controls to adjust
 * thresholds, schedule retraining of any machine learning models, or review
 * performance metrics. For now it simply informs the user that these
 * capabilities are forthcoming.
 */
export default function Learning() {
  return (
    <div className="glass" style={{ padding: 12 }}>
      <h2 style={{ fontWeight: 600, marginBottom: 8 }}>Self‑Learning Controls</h2>
      <p style={{ opacity: 0.8, fontSize: 14 }}>
        This section will host features for training and tuning the adaptive
        trading engine. Future releases may allow you to adjust regime
        thresholds, retrain pattern classifiers, or explore strategy
        performance metrics. Stay tuned!
      </p>
    </div>
  )
}