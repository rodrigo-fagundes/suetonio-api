''' Repository genérico '''
import json
import base64
import gzip
import requests
from decimal import Decimal
from impala.util import as_pandas
from pandas.io.json import json_normalize
from flask import current_app
from datasources import get_impala_connection, get_redis_pool
from service.query_builder import QueryBuilder
import numpy as np

#pylint: disable=R0903
class BaseRepository():
    ''' Generic class for repositories '''
    NAMED_QUERIES = {
        'QRY_FIND_DATASET': 'SELECT {} FROM {} {} {} {} {} {}',
        'QRY_FIND_JOINED_DATASET': 'SELECT {} FROM {} LEFT JOIN {} ON {} {} {} {}'
    }
    TABLE_NAMES = {}
    ON_JOIN = {}
    JOIN_SUFFIXES = {}
    VAL_FIELD = 'vl_indicador'
    DEFAULT_GROUPING = 'nu_competencia, cd_indicador'
    DEFAULT_PARTITIONING = 'cd_indicador'
    CNPJ_RAIZ_COLUMNS = {
        "aeronaves": "cpf_cnpj",
        "auto": "nu_cnpj_raiz",
        "rais": "nu_cnpj_raiz",
        "rfb" : "nu_cnpj_raiz",
        "rfbsocios": "nu_cnpj_raiz",
        "rfbparticipacaosocietaria": "nu_cnpj_cpf_socio",
        "sisben": "nu_cnpj_raiz",
        "catweb": {
            "empregador":{"column": "nu_cnpj_raiz_empregador", "flag": "tp_empregador"},
            "tomador": {"column": "nu_cnpj_raiz_tomador", "flag": "tp_tomador"},
            "concessao": {"column": "nu_cnpj_raiz_empregador_concessao", "flag": "tp_empregador_concessao"},
            "aeps": {"column": "nu_cnpj_raiz_empregador_aeps", "flag": "tp_empregador_aeps"},
        },
        "renavam": "nu_identificacao_prop_veic",
        "cagedsaldo": "cnpj_cei"
    }
    CNPJ_COLUMNS = {
        'aeronaves': 'cpf_cnpj',
        'auto': 'nrinscricao',
        'caged': 'cnpj_cei',
        'cagedsaldo': 'cnpj_cei',
        'cagedtrabalhador': 'cnpj_cei',
        'cagedtrabalhadorano': 'cnpj_cei',
        'rais': 'nu_cnpj_cei',
        'renavam': 'nu_identificacao_prop_veic',
        'rfb': 'nu_cnpj',
        'rfbsocios': 'nu_cnpj',
        'rfbparticipacaosocietaria': 'nu_cnpj_cpf_socio',
        'sisben': 'nu_cnpj',
        "catweb": {
            "empregador":{"column": "nu_cnpj_empregador", "flag": "tp_empregador"},
            "tomador": {"column": "tp_tomador", "flag": "nu_cnpj_tomador"},
            "concessao": {"column": "nu_cnpj_empregador_concessao", "flag": "tp_empregador_concessao"},
            "aeps": {"column": "nu_cnpj_empregador_aeps", "flag": "tp_empregador_aeps"},
        }
    } # Dados que possuem nomes diferentes para a coluna de cnpj
    COMPET_COLUMNS = {
        'auto': 'dtlavratura', # Date, used to filter
        'caged': 'competencia_declarada',
        'cagedsaldo': 'competencia_mov',
        'cagedtrabalhador': 'competencia_declarada',
        'cagedtrabalhadorano': 'ano_declarado',
        'rais': 'nu_ano_rais',
        'catweb': 'dt_acidente' # Date, used to filter
    }
    PF_COLUMNS = {
        'aeronaves': 'proprietario_cpfcnpj',
        'cagedtrabalhador': 'cpf',
        'cagedtrabalhadorano': 'cpf',
        'catweb': 'nu_nit',
        'rais': 'nu_cpf',
        'renavam': 'proprietario_cpfcnpj',
        'rfb': 'nu_cpf_responsavel',
        'rfbsocios': 'cnpj_cpf_socio',
        'rfbparticipacaosocietaria': 'cnpj_cpf_socio',
        'auto': 'nrauto'
    } # Dados que possuem nomes diferentes para a coluna de identificação da Pessoa Física
    PERSP_COLUMNS = { # Colunas que indicam diferentes perspectivas em um mesmo dataset
        'catweb': 'origem_busca'
    }
    PERSP_VALUES = {
        'catweb': {
            'empregador': 'Empregador',
            'tomador': 'Tomador',
            'concessao': 'Empregador Concessão',
            'aeps': 'Empregador AEPS'
        }
    }
    CALCS_DICT = {
        "min_part": 'MIN({val_field}) OVER(PARTITION BY {partition}) AS api_calc_{calc}',
        "max_part": 'MAX({val_field}) OVER(PARTITION BY {partition}) AS api_calc_{calc}',
        "avg_part": 'AVG({val_field}) OVER(PARTITION BY {partition}) AS api_calc_{calc}',
        "var_part": ('{val_field} - AVG({val_field}) OVER(PARTITION BY {partition}) '
                     'AS api_calc_{calc}'
                    ),
        "ln_var_part": ('LOG10({val_field} - AVG({val_field}) OVER(PARTITION BY {partition}) / '
                        'AVG({val_field}) OVER(PARTITION BY {partition}) + 1.0001) '
                        'AS api_calc_{calc}'
                       ),
        "norm_pos_part": ('CASE '
                          '(MAX({val_field}) OVER(PARTITION BY {partition}) - '
                          'MIN({val_field}) OVER(PARTITION BY {partition})) '
                          'WHEN 0 '
                          'THEN 0.5 '
                          'ELSE '
                          '({val_field} - MIN({val_field}) OVER(PARTITION BY {partition})) / '
                          '(MAX({val_field}) OVER(PARTITION BY {partition}) - '
                          'MIN({val_field}) OVER(PARTITION BY {partition})) '
                          'END '
                          'AS api_calc_{calc}'
                         ),
        "ln_norm_pos_part": ('CASE '
                             '(MAX({val_field}) OVER(PARTITION BY {partition}) - '
                             'MIN({val_field}) OVER(PARTITION BY {partition})) '
                             'WHEN 0 '
                             'THEN LOG10(1.5) '
                             'ELSE '
                             'LOG10(({val_field} - MIN({val_field}) '
                             'OVER(PARTITION BY {partition})) / '
                             '(MAX({val_field}) OVER(PARTITION BY {partition}) - '
                             'MIN({val_field}) OVER(PARTITION BY {partition})) + 1.0001) '
                             'END '
                             'AS api_calc_{calc}'
                            ),
        "norm_part": ('CASE WHEN {val_field} >= 0 '
                      'THEN {val_field} / (MAX({val_field}) OVER(PARTITION BY {partition})) '
                      'ELSE -1 * {val_field} / (MIN({val_field}) '
                      'OVER(PARTITION BY {partition})) '
                      'END AS api_calc_{calc}'
                     ),
        "ln_norm_part": ('CASE WHEN {val_field} >= 0 '
                         'THEN LOG10({val_field} / '
                         '(MAX({val_field}) OVER(PARTITION BY {partition})) + 1.0001) '
                         'ELSE -1 * LOG10({val_field} / '
                         '(MIN({val_field}) OVER(PARTITION BY {partition})) + 1.0001) '
                         'END AS api_calc_{calc}'
                        )
    }

    def __init__(self):
        ''' Construtor '''
        self.dao = self.load_and_prepare()

    def load_and_prepare(self):
        ''' Método abstrato para carregamento do dataset '''
        raise NotImplementedError("Repositórios precisam implementar load_and_prepare")

    def get_dao(self):
        ''' Garantia de que o modelo estará carregado '''
        if self.dao is None:
            self.load_and_prepare()
        return self.dao

    def get_column_defs(self, table_name):
        ''' Get the column definitions from a dataframe '''
        return {
            'cnpj_raiz': self.CNPJ_RAIZ_COLUMNS.get(table_name, 'cnpj_raiz'),
            'cnpj': self.CNPJ_COLUMNS.get(table_name, 'cnpj'),
            'pf': self.PF_COLUMNS.get(table_name, 'cpf'),
            'persp': self.PERSP_COLUMNS.get(table_name),
            'persp_options': self.PERSP_VALUES.get(table_name),
            'compet': self.COMPET_COLUMNS.get(table_name)
        }
    
    @staticmethod
    def decode_column_defs(original, table_name, perspective):
        ''' Get the column definitions from a dataframe with a certain perspective'''
        result = original.copy()

        result['cnpj_raiz'] = original.get('cnpj_raiz',{}).get(perspective,{}).get('column')
        result['cnpj_raiz_flag'] = original.get('cnpj_raiz',{}).get(perspective,{}).get('flag')
        precedence = []
        for persp_key, persp in original.get('cnpj_raiz',{}).items():
            if persp_key == perspective:
                break
            precedence.append(persp)
        result['cnpj_raiz_preceding_exclusions'] = precedence
            

        result['cnpj'] = original.get('cnpj',{}).get(perspective,{}).get('column')
        result['cnpj_flag'] = original.get('cnpj',{}).get(perspective,{}).get('flag')
        precedence = []
        for persp_key, persp in original.get('cnpj',{}).items():
            if persp_key == perspective:
                break
            precedence.append(persp)
        result['cnpj_preceding_exclusions'] = precedence
        
        return result

    def get_table_name(self, theme):
        ''' Obtém o nome de uma tabela do cloudera '''
        tbl_name = self.TABLE_NAMES.get(theme)
        if tbl_name is None:
            raise KeyError("Invalid theme")
        return tbl_name


class HadoopRepository(BaseRepository):
    '''Generic class for hive/impala repositories '''
    def fetch_data(self, query):
        ''' Runs the query in pandas '''
        cursor = self.get_dao().cursor()
        cursor.execute(query)
        df = as_pandas(cursor)
        if not df.empty:
            for col in df.columns:
                if df[col].dtype == object:
                    lst_objs = df[col].dropna()
                    if len(lst_objs) > 0 and isinstance(lst_objs.iloc[0],Decimal):
                        df[col] = df[col].astype(float)
        return df

    @staticmethod
    def build_agr_array(valor=None, agregacao=None):
        ''' Combina a agregação com o campo de valor, para juntar nos campos da query '''
        if agregacao is None or not agregacao:
            return []
        result = []
        for each_aggr in agregacao:
            agr_string = QueryBuilder.get_agr_string(each_aggr, valor)
            if agr_string is not None:
                result.append(agr_string)
        return result

    @staticmethod
    def build_generic_agr_array(agregacao=None):
        ''' Prepara agregação sem campo definido '''
        if agregacao is None or not agregacao:
            return []
        result = []
        for each_aggr in agregacao:
            agr_string = QueryBuilder.get_agr_string(each_aggr, '*')
            if agr_string is not None:
                result.append(agr_string)
        return result

    @staticmethod
    def build_order_string(ordenacao=None):
        ''' Prepara ordenação '''
        if ordenacao is None or not ordenacao:
            return ''
        if not QueryBuilder.validate_field_array(ordenacao):
            raise ValueError('Invalid aggregation')
        order_str = ''
        for field in ordenacao:
            if order_str == '':
                order_str += 'ORDER BY '
            else:
                order_str += ', '
            if "-" in field:
                order_str += field[1:] + ' DESC'
            else:
                order_str += field
        return order_str

    def build_joined_grouping_string(self, categorias=None, agregacao=None, joined=None):
        ''' Constrói o tracho da query que comanda o agrupamento '''
        if categorias is None:
            raise ValueError('Invalid fields')
        nu_cats = []
        for categoria in categorias:
            suffix = self.get_join_suffix(joined)
            if '-' in categoria:
                arr_categoria = categoria.split('-')
                if arr_categoria[0][-len(suffix):] == suffix:
                    nu_cats.append(arr_categoria[0][:-len(suffix)])
                else:
                    nu_cats.append(arr_categoria[0])
            elif categoria[-len(suffix):] == suffix:
                nu_cats.append(categoria[:-len(suffix)])
            else:
                nu_cats.append(categoria)
        if agregacao is not None:
            if QueryBuilder.is_valid_grouping(agregacao):
                return f'GROUP BY {", ".join(nu_cats)}'
            return ''
        raise ValueError('Invalid aggregation (no value)')

    def load_and_prepare(self):
        ''' Método abstrato para carregamento do dataset '''
        raise NotImplementedError("Repositórios precisam implementar load_and_prepare")

    def get_named_query(self, query_name):
        ''' Obtém uma string parametrizada de query '''
        qry_dict = self.NAMED_QUERIES
        return qry_dict[query_name]

    def get_join_condition(self, table_name, join_clauses=None):
        ''' Obtém a condição do join das tabelas '''

    def get_join_suffix(self, table_name):
        ''' Obtém uma string de sufixo de campo de tabela juntada '''
        on_suffix_dict = self.JOIN_SUFFIXES
        return on_suffix_dict[table_name]

    def build_categorias(self, categorias, options):
        ''' Constrói a parte dos atributos selecionados na query '''
        if not QueryBuilder.check_params(options, ['categorias']):
            raise ValueError('Invalid Categories - required')
        categorias = QueryBuilder.transform_categorias(categorias)
        prepended_aggr = QueryBuilder.prepend_aggregations(options.get('agregacao'))
        str_calcs = ''
        if QueryBuilder.check_params(options, ['calcs']):
            calcs_options = options.copy()
            calcs_options['categorias'] = categorias
            str_calcs += self.build_std_calcs(calcs_options)
        if QueryBuilder.check_params(options, ['agregacao', 'valor']):
            tmp_cats = self.combine_val_aggr(options.get('valor'), options.get('agregacao'))
            if not isinstance(tmp_cats, list):
                categorias += tmp_cats.split(", ")
            else:
                categorias += tmp_cats
        elif (not QueryBuilder.check_params(options, ['agregacao']) and
              QueryBuilder.check_params(options, ['valor'])):
            categorias += options['valor']
        elif (QueryBuilder.check_params(options, ['agregacao']) and
              not QueryBuilder.check_params(options, ['valor'])):
            categorias += self.build_generic_agr_array(options['agregacao'])
        if (QueryBuilder.validate_field_array(categorias) and
                QueryBuilder.validate_field_array(prepended_aggr)):
            if 'calcs' not in options or options['calcs'] is None or str_calcs == '':
                return ' '.join(prepended_aggr) + ' ' + ', '.join(categorias)
            return ' '.join(prepended_aggr) + ' ' + ', '.join(categorias) + ', ' + str_calcs
        raise ValueError('Invalid attributes')

    def build_std_calcs(self, options):
        '''Constrói campos calculados de valor, como min, max e normalizado '''
        if self.VAL_FIELD is None or self.get_default_partitioning(options) is None:
            return ''

        # Pega o valor passado ou padrão, para montar a query
        val_field = self.VAL_FIELD
        if QueryBuilder.check_params(options, ['valor']):
            val_field = options['valor']

        # Pega o valor do particionamento
        if not QueryBuilder.check_params(options, ['partition']):
            if self.get_default_partitioning(options) != '':
                res_partition = self.get_default_partitioning(options)
            else:
                res_partition = "'1'"
        else:
            res_partition = options['partition']

        # Transforma o campo de valor em campo agregado conforme query
        if QueryBuilder.check_params(options, ['agregacao']):
            val_field = QueryBuilder.get_simple_agr_string(
                options['agregacao'][0],
                options['valor'][0]
            )
            if QueryBuilder.check_params(options, ['pivot']):
                res_partition = self.exclude_from_partition(
                    options['categorias'],
                    options['agregacao']
                )

        str_res_partition = res_partition
        if isinstance(res_partition, list):
            str_res_partition = ",".join(res_partition)

        # Constrói a query
        arr_calcs = []
        for calc in options['calcs']:
            # Always appends min and max when calc is not one of them
            if calc not in ['min_part', 'max_part']:
                arr_calcs.append(
                    self.replace_partition('min_part').format(
                        val_field=val_field,
                        partition=str_res_partition,
                        calc='min_part'
                    )
                )
                arr_calcs.append(
                    self.replace_partition('max_part').format(
                        val_field=val_field,
                        partition=str_res_partition,
                        calc='max_part'
                    )
                )
            # Resumes identification of calc
            arr_calcs.append(
                self.replace_partition(calc, options).format(
                    val_field=val_field,
                    partition=str_res_partition,
                    calc=calc
                )
            )
        return ', '.join(arr_calcs)

    def replace_partition(self, qry_part, options=None):
        ''' Changes OVER clause when there's no partitioning '''
        if self.get_default_partitioning(options) == '':
            return self.CALCS_DICT[qry_part].replace("PARTITION BY {partition}", "")
        return self.CALCS_DICT[qry_part]

    def exclude_from_partition(self, categorias, agregacoes, options=None):
        ''' Remove do partition as categorias não geradas pela agregação '''
        partitions = self.get_default_partitioning(options).split(", ")
        groups = QueryBuilder.build_grouping_string(categorias, agregacoes).replace(
            'GROUP BY ', ''
        ).split(", ")
        result = []
        for partition in partitions:
            if partition in groups:
                result.append(partition)
        return ", ".join(result)

    def get_default_partitioning(self, _options):
        ''' Default method for getting partitioning '''
        return self.DEFAULT_PARTITIONING

    def combine_val_aggr(self, valor, agregacao, suffix=None):
        ''' Combina valores e agregções para construir a string correta '''
        if len(valor) == 1:
            if suffix is not None and valor[0][-len(suffix):] == suffix:
                return self.build_agr_array(valor[0][:-len(suffix)], agregacao)
            return self.build_agr_array(valor[0], agregacao)
        result = ''
        for indx, val in enumerate(valor):
            if indx > 0:
                result += ', '
            aux_val = val
            if suffix is not None and val[-len(suffix):] == suffix:
                aux_val = val[:-len(suffix)]
            if len(agregacao) == 1:
                agrs = agregacao[0].split('-')
                result += ', '.join(self.build_agr_array(aux_val, agrs))
            else:
                agrs = agregacao[indx].split('-')
                result += ', '.join(self.build_agr_array(aux_val, agrs))
        return result

    def build_joined_categorias(self, categorias, valor=None, agregacao=None,
                                joined=None):
        ''' Constrói a parte dos atributos selecionados na query '''
        if categorias is None or not categorias:
            raise ValueError('Invalid Categories - required')
        str_cat = []
        suffix = self.get_join_suffix(joined)
        str_cat += QueryBuilder.transform_joined_categorias(categorias, suffix)
        if agregacao is not None and valor is not None:
            str_cat += self.combine_val_aggr(valor, agregacao, suffix)
        elif agregacao is not None and valor is None:
            str_cat += self.build_generic_agr_array(agregacao)
        if QueryBuilder.validate_field_array(str_cat):
            return ', '.join(str_cat)
        raise ValueError('Invalid attributes')

    def build_filter_string(self, where=None, joined=None, is_on=False):
        ''' Builds WHERE clauses or added ON conditions '''
        simple_operators = {
            'EQ': "=", "NE": "!=", "LE": "<=", "LT": "<", "GE": ">=",
            "GT": ">", "LK": "LIKE"
        }
        boolean_operators = {
            "NL": "IS NULL", "NN": "IS NOT NULL"
        }
        suffix = ''
        if joined is not None:
            suffix = self.get_join_suffix(joined)
        if where is None or (joined is None and is_on):
            return ''
        arr_result = []
        for each_clause in where:
            w_clause = each_clause.replace('\\-', '|')
            w_clause = w_clause.split('-')
            w_clause = [f.replace('|', '-') for f in w_clause]
            if w_clause[0].upper() == 'AND' or w_clause[0].upper() == 'OR':
                arr_result.append(w_clause[0])
            elif QueryBuilder.validate_clause(w_clause, joined, is_on, suffix):
                if w_clause[0].upper() in simple_operators:
                    arr_result.append(
                        f'{w_clause[1]} '
                        f'{simple_operators[w_clause[0].upper()]} '
                        f'{w_clause[2]}'
                    )
                elif w_clause[0].upper() in boolean_operators:
                    arr_result.append(
                        f'{w_clause[1]} '
                        f'{boolean_operators[w_clause[0].upper()]}'
                    )
                elif w_clause[0].upper() == 'IN':
                    arr_result.append(f'{w_clause[1]} IN ({",".join(w_clause[2:])})')
                elif w_clause[0].upper() in ['EQON', 'NEON', 'LEON', 'GEON', 'LTON', 'GTON']:
                    resulting_string = f"regexp_replace(CAST({w_clause[1]} AS STRING), '[^[:digit:]]','')"
                    if len(w_clause) == 5: # Substring
                        resulting_string = f"substring({resulting_string}, {w_clause[3]}, {w_clause[4]})" 
                    arr_result.append(f"{resulting_string} {simple_operators.get(w_clause[0].upper()[:2])} '{w_clause[2]}'")
                elif w_clause[0].upper() in ['EQLPONSTR', 'NELPONSTR', 'LELPONSTR', 'GELPONSTR', 'LTLPONSTR', 'GTLPONSTR']:
                    resulting_string = f"regexp_replace(CAST({w_clause[1]} AS STRING), '[^[:digit:]]','')"
                    if len(w_clause) == 7: # Substring
                        resulting_string = f"substring(LPAD({resulting_string}, {w_clause[3]}, '{w_clause[4]}'), {w_clause[5]}, {w_clause[6]})"
                    arr_result.append(f"{resulting_string} {simple_operators.get(w_clause[0].upper()[:2])} '{w_clause[2]}'")
                elif w_clause[0].upper() in ['EQSTR', 'NESTR', 'LESTR', 'GESTR', 'LTSTR', 'GTSTR']:
                    arr_result.append(f"substring(CAST({w_clause[1]} AS STRING), {w_clause[3]}, {w_clause[4]}) {simple_operators.get(w_clause[0].upper()[:2])} {w_clause[2]}")
                elif w_clause[0].upper() in ['EQLPSTR', 'NELPSTR', 'LELPSTR', 'GELPSTR', 'LTLPSTR', 'GTLPSTR']:
                    arr_result.append(f"substring(LPAD(CAST({w_clause[1]} AS VARCHAR({w_clause[3]})), {w_clause[3]}, '{w_clause[4]}'), {w_clause[5]}, {w_clause[6]}) {simple_operators.get(w_clause[0].upper()[:2])} {w_clause[2]}")
                elif w_clause[0].upper() in ['EQLPINT', 'NELPINT', 'LELPINT', 'GELPINT', 'LTLPINT', 'GTLPINT']:
                    arr_result.append(f"CAST(substring(LPAD(CAST({w_clause[1]} AS VARCHAR({w_clause[3]})), {w_clause[3]}, '{w_clause[4]}'), {w_clause[5]}, {w_clause[6]}) AS INTEGER) {simple_operators.get(w_clause[0].upper()[:2])} {w_clause[2]}")
                elif w_clause[0].upper() in ['EQSZ', 'NESZ', 'LESZ', 'GESZ', 'LTSZ', 'GTSZ']:
                    arr_result.append(f"LENGTH(CAST({w_clause[1]} AS STRING)) {simple_operators.get(w_clause[0].upper()[:2])} {w_clause[2]}")
        return ' '.join(arr_result)

    @staticmethod
    def get_agr_string(agregacao, valor):
        ''' Proxy for Query Builder function call '''
        return QueryBuilder.get_agr_string(agregacao, valor)

    def find_dataset(self, options=None):
        ''' Obtém dataset de acordo com os parâmetros informados '''
        if QueryBuilder.catch_injection(options):
            raise ValueError('SQL reserved words not allowed!')
        str_where = ''
        if options.get('where') is not None:
            str_where = ' WHERE ' + self.build_filter_string(options.get('where'))
        str_group = ''
        nu_cats = options['categorias']
        if options.get('pivot') is not None:
            nu_cats = nu_cats + options['pivot']
        if options.get('agregacao', False):
            str_group = QueryBuilder.build_grouping_string(
                nu_cats,
                options['agregacao']
            )
        str_categorias = self.build_categorias(nu_cats, options)
        str_limit = ''
        if options.get('limit') is not None:
            str_limit = f'LIMIT {options.get("limit")}'
        str_offset = ''
        if options.get('offset') is not None:
            str_offset = f'OFFSET {options.get("offset")}'
        if 'theme' not in options:
            options['theme'] = 'MAIN'

        query = self.get_named_query('QRY_FIND_DATASET').format(
            str_categorias,
            self.get_table_name(options['theme']),
            str_where,
            str_group,
            self.build_order_string(options.get('ordenacao')),
            str_limit,
            str_offset
        )
        return self.fetch_data(query)

    def find_joined_dataset(self, options=None):
        ''' Obtém dataset de acordo com os parâmetros informados '''
        if QueryBuilder.catch_injection(options):
            raise ValueError('SQL reserved words not allowed!')
        if options['joined'] is None:
            raise ValueError('Joined table is required')
        str_where = ''
        if options['where'] is not None:
            str_where = ' WHERE ' + self.build_filter_string(options['where'], options['joined'],
                                                             False)
        str_group = ''
        if options['agregacao'] is not None:
            str_group = self.build_joined_grouping_string(
                options['categorias'],
                options['agregacao'],
                options['joined']
            )
        if 'theme' not in options:
            options['theme'] = 'MAIN'
        str_categorias = self.build_joined_categorias(options['categorias'], options['valor'],
                                                      options['agregacao'], options['joined'])
        query = self.get_named_query('QRY_FIND_JOINED_DATASET').format(
            str_categorias,
            self.get_table_name(options.get('theme')), # FROM
            self.get_table_name(options.get('joined')), # JOIN
            self.get_join_condition(options['joined'], options['where']), # ON
            str_where, # WHERE
            str_group, # GROUP BY
            self.build_order_string(options.get('ordenacao')) # ORDER BY
        )

        return self.fetch_data(query)

class ImpalaRepository(HadoopRepository):
    '''Generic class for impala repositories '''
    def load_and_prepare(self):
        ''' Prepara o DAO '''
        self.dao = get_impala_connection()

class HBaseRepository(BaseRepository):
    ''' HBase connector class '''
    def load_and_prepare(self): # No DAO - http request
        ''' Prepara o DAO '''
        pass

    @staticmethod
    def fetch_data(table, key, column_family, column):
        ''' Gets data from HBase instance '''
        url = "http://{}:{}/{}/{}".format(
            current_app.config["HBASE_HOST"],
            current_app.config["HBASE_PORT"],
            table,
            key
        )
        if column_family is not None:
            url = url + "/" + str(column_family)
            if column is not None:
                url = url + ":" + str(column)

        response = requests.get(url, headers={'Accept': 'application/json'})
        # If the response was successful, no Exception will be raised
        response.raise_for_status()

        return json.loads(response.content)['Row']

    def find_row(self, table, key, column_family, column):
        ''' Obtém dataset de acordo com os parâmetros informados '''
        # Makes sure the returning data will be a JSON
        result = {}
        for row_key in self.fetch_data(table, key, column_family, column):
            for col in row_key['Cell']:
                colfam = base64.urlsafe_b64decode(col['column'])
                column_parts = colfam.decode('UTF-8').split(':')

                # Decompressing gzip hbase value
                value = gzip.decompress(base64.urlsafe_b64decode(col['$']))
                # Replacing double-quotes
                str_value = value.decode('UTF-8').replace("\\xe2\\x80\\x9", '"')
                # Turn value to pandas dataset
                dataset = json_normalize(json.loads(str_value))
                dataset['col_compet'] = column_parts[1]

                # Append do existing dataset or create a new one
                if column_parts[0] in result:
                    result[column_parts[0]] = result[column_parts[0]].append(
                        dataset, ignore_index=True
                    )
                else:
                    result[column_parts[0]] = dataset

        return result

class RedisRepository(BaseRepository):
    ''' Generic class for redis repositories '''
    def load_and_prepare(self):
        ''' Prepara o DAO '''
        self.dao = get_redis_pool()

    def retrieve_hashset(self, key):
        ''' Localiza o dicionário de datasources no REDIS '''
        return {
            key.decode(): value.decode()
            for
            (key, value)
            in
            self.get_dao().hgetall(key).items()
        }
