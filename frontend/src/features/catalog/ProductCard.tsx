import {useState} from 'react';
import {Link} from 'react-router-dom';
import type {Product} from './types';

export function ProductCard({product, categoryName}: {product: Product; categoryName: string}) {
  const [failedImage, setFailedImage] = useState<string | null>(null);
  const image = product.image_url;
  return <article className="product-card">
    <div className="product-image">
      {image && failedImage !== image
        ? <img src={image} alt={product.name} onError={() => setFailedImage(image)}/>
        : <div className="image-placeholder"><span aria-hidden="true">Б</span><span>Нет изображения</span></div>}
    </div>
    <div className="product-copy"><p className="product-category">{categoryName}</p><h2><Link to={`/products/${product.id}`} style={{textDecoration: 'none'}}>{product.name}</Link></h2>
      {(product.brand || product.model) && <p className="product-model">{[product.brand, product.model].filter(Boolean).join(' · ')}</p>}
    </div>
  </article>;
}
