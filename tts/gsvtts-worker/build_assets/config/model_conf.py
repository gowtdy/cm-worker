def get_model_conf(key):
    model_conf = {
        'bert_model_path': 'models/pretrained_models/chinese-roberta-wwm-ext-large',
        'cnhubert_model_path': 'models/pretrained_models/chinese-hubert-base',
        'sv_model_path': 'models/pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt',
        
    }
    return model_conf[key]