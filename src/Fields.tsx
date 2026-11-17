import { TextField, TextFieldProps } from "@decky/ui";
import { useEffect, useRef, useState } from "react";

type Props = Omit<TextFieldProps, "value" | "onChange" | "onBlur"> & {
  value: string;
  onCommit: (value: string) => void;
  /** Report to the parent this long after the last key; 0 = only when the field loses focus. */
  delay?: number;
};

/**
 * A text field that keeps what is typed to itself and tells the parent only after a pause
 * or on blur. The Steam on-screen keyboard drops keys when the whole panel redraws on every
 * letter, so only this small component re-renders while typing.
 */
export function LazyTextField({ value, onCommit, delay = 500, ...rest }: Props) {
  const [text, setText] = useState(value);
  const known = useRef(value); // what the parent last saw
  const typed = useRef(value);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const commitRef = useRef(onCommit);
  commitRef.current = onCommit;

  const commit = () => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    if (typed.current !== known.current) {
      known.current = typed.current;
      commitRef.current(typed.current);
    }
  };

  // The parent changed the value itself (cleared it, dictation filled it in).
  // Never while the player has typed something the parent hasn't seen yet.
  useEffect(() => {
    if (value !== known.current && typed.current === known.current) {
      known.current = typed.current = value;
      setText(value);
    }
  }, [value]);

  useEffect(() => () => commit(), []);

  return (
    <TextField
      {...rest}
      value={text}
      onChange={(e) => {
        typed.current = e.target.value;
        setText(e.target.value);
        if (delay > 0) {
          if (timer.current) clearTimeout(timer.current);
          timer.current = setTimeout(commit, delay);
        }
      }}
      onBlur={commit}
    />
  );
}
