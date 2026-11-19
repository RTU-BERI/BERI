import {api, ApiError} from '../../shared/api/client';
import type {ApiMetadata} from '../../shared/api/client';
import type {Clock, CreateReservationInput, Reservation, ReservationResult} from './types';

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const isId = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
const isUuid = (value: unknown) => typeof value === 'string' && /^[\da-f]{8}-(?:[\da-f]{4}-){3}[\da-f]{12}$/i.test(value);
const isDate = (value: unknown) => typeof value === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
const nullableDate = (value: unknown) => value === null || isDate(value);

function contractError(): never {throw new ApiError('invalid_reservation_response', 'Сервер вернул неожиданный формат бронирования. Повторите проверку.');}
function parseReservation(value: unknown): Reservation {
  if (!isRecord(value) || !isUuid(value.id) || !isId(value.product_id) || !isId(value.pickup_point_id)
    || !(value.assigned_product_item_id === null || isId(value.assigned_product_item_id))
    || !['active', 'cancelled', 'expired', 'fulfilled'].includes(String(value.status))
    || !isDate(value.created_at) || !isDate(value.expires_at)
    || !nullableDate(value.cancelled_at) || !nullableDate(value.completed_at)) contractError();
  // Deliberately discard customer_phone and any other fields the UI does not need.
  return {id: value.id as string, product_id: value.product_id as number, pickup_point_id: value.pickup_point_id as number,
    assigned_product_item_id: value.assigned_product_item_id as number | null, status: value.status as Reservation['status'],
    created_at: value.created_at as string, expires_at: value.expires_at as string,
    cancelled_at: value.cancelled_at as string | null, completed_at: value.completed_at as string | null};
}

function clockFrom(metadata?: ApiMetadata): Clock {
  return metadata?.serverDate !== null && metadata?.serverDate !== undefined
    ? {epoch: metadata.serverDate + metadata.roundTripMs / 2, monotonic: metadata.receivedAt, source: 'server'}
    : {epoch: Date.now(), monotonic: performance.now(), source: 'device'};
}
export const clockNow = (clock: Clock) => clock.epoch + performance.now() - clock.monotonic;
export const formatReservationDate = (value: string) => new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
}).format(new Date(value));

export async function getReservations(): Promise<{reservations: Reservation[]; clock: Clock}> {
  let metadata: ApiMetadata | undefined;
  const data = await api<unknown>('/api/reservations/', 'GET', undefined, value => {metadata = value;});
  if (!Array.isArray(data)) contractError();
  const reservations = data.map(parseReservation);
  if (new Set(reservations.map(item => item.id)).size !== reservations.length) contractError();
  return {reservations, clock: clockFrom(metadata)};
}
export async function createReservation(input: CreateReservationInput): Promise<ReservationResult> {
  let metadata: ApiMetadata | undefined;
  const data = await api<unknown>('/api/reservations/', 'POST', input, value => {metadata = value;});
  const reservation = parseReservation(data);
  if (reservation.id !== input.request_id || reservation.product_id !== input.product_id
    || reservation.pickup_point_id !== input.pickup_point_id) contractError();
  return {reservation, clock: clockFrom(metadata)};
}
export const cancelReservation = (id: string) => api<void>(`/api/reservations/${id}/cancel/`, 'POST');

const stockMessages = new Set(['В этом пункте выдачи нет свободных экземпляров.', 'Экземпляр уже закреплён за другой бронью.']);
export function reservationError(error: unknown): string {
  if (!(error instanceof ApiError)) return 'Не удалось выполнить запрос. Повторите попытку.';
  if (error.status === 409 && stockMessages.has(error.message)) return 'К сожалению, товар уже забронирован другим пользователем. Выберите другой пункт выдачи или попробуйте позже';
  if (error.status === 401) return 'Сессия завершилась. Войдите снова.';
  if (error.status === 403) return error.code === 'csrf_failed' ? error.message : 'Не удалось подтвердить право на операцию. Проверьте сессию или обновите страницу.';
  if (error.status === 404) return 'Бронирование или выбранный объект недоступны. Обновите данные.';
  if (error.status >= 500) return 'Сервис бронирования временно недоступен. Повторите попытку позже.';
  return error.message;
}
