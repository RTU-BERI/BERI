export type User = {id: string; phone: string; account_type: 'customer' | 'employee'; phone_verified: boolean};
export type Session = {authenticated: boolean; user: User | null};
export type LoginInput = {phone: string; password: string};
export type RegisterInput = LoginInput & {password_confirm: string};
export type {FieldErrors} from '../../shared/api/client';
