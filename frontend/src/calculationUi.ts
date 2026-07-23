export type CalculationCancelAction = 'review' | 'current' | null;

export function isCancellationInProgress(
  action: CalculationCancelAction,
  cancelRequested: boolean,
): boolean {
  return action !== null || cancelRequested;
}

export function cancellationDetail(
  action: CalculationCancelAction,
  bestResultAvailable: boolean,
): string {
  if (action === 'review') {
    return 'Solver se ustavlja. Nato se bodo odprle možnosti za nadaljevanje ali uporabo zadnje rešitve.';
  }
  if (bestResultAvailable) {
    return 'Solver se ustavlja. Nato bo prikazana zadnja najdena rešitev.';
  }
  return 'Solver se ustavlja. Če rešitev še ni bila najdena, rezultat ne bo prikazan.';
}
