import os
from PIL import Image
Image.MAX_IMAGE_PIXELS = None

# Diretório onde as imagens estão armazenadas
image_dir = 'C:\\Users\\Marcelo\\Pictures\\SGs'

# Limite de pixels para considerar uma imagem como 'gigante'
pixel_limit = 89478485

# Lista para armazenar as imagens gigantes
giant_images = []

# Percorra todos os arquivos no diretório
for filename in os.listdir(image_dir):
    filepath = os.path.join(image_dir, filename)
    
    # Verifique se o arquivo é uma imagem
    try:
        with Image.open(filepath) as img:
            # Verifique se a imagem é 'gigante'
            if img.width * img.height > pixel_limit:
                print(f'A imagem {filename} é gigante.')
                giant_images.append(filepath)
    except IOError:
        # O arquivo não é uma imagem, ignore-o
        pass

# Agora percorra cada imagem gigante e pergunte ao usuário se ele deseja abri-la ou excluí-la
for filepath in giant_images:
    # Pergunte ao usuário se ele deseja abrir a imagem
    open_image = input(f'Deseja abrir a imagem {filepath}? (s/n) ')
    if open_image.lower() == 's':
        # Abra a imagem com o programa padrão
        os.startfile(filepath)

        # Pergunte ao usuário se ele deseja excluir a imagem
        delete_image = input('Deseja excluir esta imagem? (s/n) ')
        if delete_image.lower() == 's':
            # Exclua a imagem
            os.remove(filepath)
            print(f'A imagem {filepath} foi excluída.')
