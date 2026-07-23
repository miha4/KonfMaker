export type NumberInputKeyEvent = {
  key: string;
  preventDefault: () => void;
};

export function preventNumberInputArrowStep(event: NumberInputKeyEvent): void {
  if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
    event.preventDefault();
  }
}
