export type ReservationStatus = 'active' | 'cancelled' | 'expired' | 'fulfilled';
export type Reservation = {
  id: string;
  product_id: number;
  pickup_point_id: number;
  assigned_product_item_id: number | null;
  status: ReservationStatus;
  created_at: string;
  expires_at: string;
  cancelled_at: string | null;
  completed_at: string | null;
};
export type CreateReservationInput = {product_id: number; pickup_point_id: number; request_id: string};
export type Clock = {epoch: number; monotonic: number; source: 'server' | 'device'};
export type ReservationResult = {reservation: Reservation; clock: Clock};
export const statusLabels: Record<ReservationStatus, string> = {
  active: 'Активно', cancelled: 'Отменено', expired: 'Истекло', fulfilled: 'Выполнено',
};
