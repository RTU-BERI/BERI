export type Category = {id: number; name: string};

// Поля именно списка /api/products/, а не подробной страницы S1-P6.
export type Product = {
  id: number;
  name: string;
  category: number;
  status: string;
  image_url: string | null;
  brand: string | null;
  model: string | null;
};
