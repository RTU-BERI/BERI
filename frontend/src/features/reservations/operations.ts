import {createReservation} from './api';
import type {CreateReservationInput, ReservationResult} from './types';

export type Operation = {input: CreateReservationInput; pending?: Promise<ReservationResult>; result?: ReservationResult};
// Survives route changes within this tab. No phones, credentials or browser storage.
const operations = new Map<string, Operation>();
export const getOperation = (accountId: string) => operations.get(accountId);
export const forgetOperation = (accountId: string) => operations.delete(accountId);
export function clearReservationOperations() {operations.clear();}
export function beginOperation(accountId: string, productId: number, pickupPointId: number): Operation {
  const existing = operations.get(accountId);
  if (existing) return existing;
  const operation = {input: {product_id: productId, pickup_point_id: pickupPointId, request_id: crypto.randomUUID()}};
  operations.set(accountId, operation);
  return operation;
}
export function sendOperation(operation: Operation): Promise<ReservationResult> {
  if (operation.result) return Promise.resolve(operation.result);
  if (operation.pending) return operation.pending;
  operation.pending = createReservation(operation.input).then(result => {operation.result = result; return result;})
    .finally(() => {operation.pending = undefined;});
  return operation.pending;
}
