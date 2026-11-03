import type {Category, Product} from '../catalog/types';

export type ProductAttribute = {
  name: string;
  unit: string | null;
  value_text: string | null;
  value_number: string | null;
  value_boolean: boolean | null;
};

export type ProductComponent = {name: string; quantity: number};
export type ProductTariff = {hourly_rate: string; valid_from: string; valid_to: string | null};

// Detail uses a nested category; the list uses only its ID.
export type ProductDetail = Omit<Product, 'category'> & {
  description: string | null;
  category: Category;
  attributes: ProductAttribute[];
  components: ProductComponent[];
  tariff: ProductTariff | null;
};

// Coordinates and working hours are returned too, but are not needed by this page.
export type PickupPoint = {id: number; name: string; address: string; status: string};
export type ProductAvailability = {
  product_id: number;
  pickup_points: {pickup_point_id: number; available_count: number}[];
};
