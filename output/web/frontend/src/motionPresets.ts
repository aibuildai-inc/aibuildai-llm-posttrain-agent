// The one motion-language knob. Reduced motion means IMMEDIATE: a user who
// asks for reduced motion gets the final state at once (duration 0), not a
// slower or partial animation. MotionConfig handles transform and layout
// motion globally. This hook also removes opacity motion where a component
// must land its final state at once.
import type { Transition } from "motion/react";
import { useReducedMotion } from "motion/react";

export function useMotionTransition(base: Transition): Transition {
  const reduce = useReducedMotion();
  return reduce === true ? { duration: 0 } : base;
}
