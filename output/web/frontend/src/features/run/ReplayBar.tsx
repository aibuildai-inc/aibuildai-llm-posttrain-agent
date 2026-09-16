// One browser-local timeline. The server owns journal frames only; this bar
// owns the playhead, playing state, speed, and drag state.
import { Pause, Play, SkipBack, SkipForward } from "lucide-react";
import { useState, type ReactNode } from "react";
import {
  Button,
  Label,
  ListBox,
  ListBoxItem,
  Popover,
  Select,
  SelectValue,
  Slider,
  SliderOutput,
  SliderThumb,
  SliderTrack,
} from "react-aria-components";
import { fmtHhmmss } from "../../format";

const SPEEDS = [0.5, 1, 2, 5, 10];

export function TimelineBar(props: {
  headS: number;
  positionS: number;
  playing: boolean;
  speed: number;
  onPosition: (position: number) => void;
  onPlaying: (playing: boolean) => void;
  onSpeed: (speed: number) => void;
}): ReactNode {
  const [drag, setDrag] = useState<number | null>(null);
  const position = drag ?? props.positionS;
  const play = (): void => props.onPlaying(!props.playing);
  return (
    <div className="replay-bar" aria-label="Run timeline">
      <Button
        className="replay-button"
        aria-label="Return to Run birth"
        onPress={() => props.onPosition(0)}
      >
        <SkipBack size={15} strokeWidth={1.75} aria-hidden />
      </Button>
      <Button
        className="replay-button"
        aria-label={props.playing ? "Pause timeline" : "Play timeline"}
        onPress={play}
      >
        {props.playing ? (
          <Pause size={15} strokeWidth={1.75} aria-hidden />
        ) : (
          <Play size={15} strokeWidth={1.75} aria-hidden />
        )}
      </Button>
      <span className="replay-clock num">
        {fmtHhmmss(position)} / {fmtHhmmss(props.headS)}
      </span>
      <Slider
        className="replay-scrubber"
        aria-label="Timeline position"
        minValue={0}
        maxValue={Math.max(props.headS, 1)}
        step={1}
        value={position}
        onChange={(value) => setDrag(value as number)}
        onChangeEnd={(value) => {
          setDrag(null);
          props.onPosition(value as number);
        }}
      >
        <SliderOutput className="visually-hidden" />
        <SliderTrack className="replay-track">
          {({ state }) => (
            <>
              <div
                className="replay-track-fill"
                style={{ width: `${state.getThumbPercent(0) * 100}%` }}
              />
              <SliderThumb className="replay-thumb" />
            </>
          )}
        </SliderTrack>
      </Slider>
      <Select
        className="replay-speed"
        aria-label="Timeline speed"
        selectedKey={String(props.speed)}
        onSelectionChange={(key) => props.onSpeed(Number(key))}
      >
        <Label className="visually-hidden">Timeline speed</Label>
        <Button className="run-select-trigger replay-speed-trigger">
          <SelectValue />
        </Button>
        <Popover className="run-select-popover replay-speed-popover">
          <ListBox className="run-select-list">
            {SPEEDS.map((speed) => (
              <ListBoxItem
                key={speed}
                id={String(speed)}
                className="run-select-row"
                textValue={`${speed}x`}
              >
                {speed}×
              </ListBoxItem>
            ))}
          </ListBox>
        </Popover>
      </Select>
      <Button
        className="replay-button"
        aria-label="Follow the Run head"
        onPress={() => props.onPosition(props.headS)}
      >
        <SkipForward size={15} strokeWidth={1.75} aria-hidden />
      </Button>
    </div>
  );
}
