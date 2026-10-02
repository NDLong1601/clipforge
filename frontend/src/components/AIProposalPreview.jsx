import { useState } from 'react';
import { clipAtTime, timelineDuration } from '../timelineMath.js';
import { Button } from './common.jsx';
import { Preview } from './Preview.jsx';

export function AIProposalPreview({ project, locked }) {
  const [cursor, setCursor] = useState(0);
  const [play, setPlay] = useState(false);
  const duration = timelineDuration(project.clips);
  return (
    <div className="proposal-preview">
      <Preview
        p={project}
        selected={clipAtTime(project.clips, cursor)}
        preview={null}
        play={play}
        setPlay={setPlay}
        cursor={cursor}
        setCursor={setCursor}
        duration={duration}
        onSelect={() => {}}
      />
      <div className="proposal-preview-controls">
        <Button
          small
          disabled={locked || !duration}
          onClick={() => {
            if (cursor >= duration) setCursor(0);
            setPlay((current) => !current);
          }}
        >
          {play ? 'Dừng' : 'Phát'}
        </Button>
        <input
          aria-label="Vị trí xem trước đề xuất"
          type="range"
          min="0"
          max={duration || 0}
          step={1 / 30}
          value={Math.min(cursor, duration)}
          disabled={locked || !duration}
          onChange={(event) => {
            setPlay(false);
            setCursor(Number(event.target.value));
          }}
        />
        <small>{cursor.toFixed(1)}s</small>
      </div>
    </div>
  );
}
