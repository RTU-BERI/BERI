import {useState} from 'react';

export function ProductImage({image, name}: {image: string | null; name: string}) {
  const [failedImage, setFailedImage] = useState<string | null>(null);
  return <div className="product-image product-page__image">
    {image && failedImage !== image
      ? <img src={image} alt={name} onError={() => setFailedImage(image)}/>
      : <div className="image-placeholder"><span aria-hidden="true">Б</span><span>Нет изображения</span></div>}
  </div>;
}
