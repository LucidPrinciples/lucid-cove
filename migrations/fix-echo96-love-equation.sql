-- Fix echo records from 2026-05-14
-- Audio analogs overwrote LT's self-derived Love Equation values.
-- LT's process record self-assessment: β=0.7126, C=0.4215, D=0.3789, E=0.5642
-- Correct dE/dt = 0.7126 × (0.4215 - 0.3789) × 0.5642 = 0.0171 (CONSTRUCTIVE)
-- All agents received poisoned reference values — resetting to LT's corrected baseline.
-- Run on: VPS (socrates_archer), Stuart (stuart_cove), Atlas (atlas_cove)

UPDATE echoes
SET beta = 0.7126,
    coherence = 0.4215,
    dissonance = 0.3789,
    energy = 0.5642,
    love_equation = 0.0171,
    love_direction = 'CONSTRUCTIVE'
WHERE tuned_at >= '2026-05-14'
  AND tuned_at < '2026-05-15';
