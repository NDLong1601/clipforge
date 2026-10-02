import { useState } from 'react';

export function usePreviewState() {
  const [preview, setPreview] = useState(null);
  const [play, setPlay] = useState(false);
  const [cursor, setCursor] = useState(0);
  const resetPreview = () => {
    setPreview(null);
    setPlay(false);
    setCursor(0);
  };
  return { preview, setPreview, play, setPlay, cursor, setCursor, resetPreview };
}
