import math

import pandas as pd
import numpy as np
import datasets

from scrc.dataset_creation.dataset_creator import DatasetCreator
from pathlib import Path
from scrc.dataset_creation.report_creator import ReportCreator
from scrc.enums.section import Section
from scrc.utils.main_utils import get_config
from scrc.utils.log_utils import get_logger
from collections import Counter
from scrc.utils.sql_select_utils import get_legal_area_bger
from scrc.enums.split import Split
from scrc.dataset_creation.report_creator import plot_input_length

"""
Let it run:
- run bge_reference_extractor (spider CH_BGE needs to be deleted in progress file)
- run bger_citations_extractor (spider CH_BGE and CH_BGer need to be deleted in progress file)
- ready to run criticality_prediction
"""


"""
Dataset to be created:
- contains supreme court cases  
- cols = year, law_area, origin_region, origin_canton, considerations, facts, language, citation_label, bge_label
- only cases where feature_col =(facts, considerations) text has good length
- Dataset Split description:
    - train 2002-2015
    - val 2016-2017
    - test 2018-2022
    - To consider: shuffle?
Set Labels
    - bge_label {critical, non-critical}
        - extract bger_file_numbers from bge (bge_reference_extractor)
        - set label critical for those found bger
    - citation_label {critical-0, critical-1, critical-2, critical-3, non-critical}
        - count citations of bge in bger cases
        - use quartils to create 4 different classes of citation amounts
        - get bger case for cited bge
        - set labels critical-{1-4} for those found bger
        - Distinct between citations depending how old they are
        ATTENTION citations criticality is a subset of bge criticality since only bge get cited
    - bger_citation_label 
        - count citations of bger in all bger (bger-citations-extractor)
        - use quartils to create 4 different classes
        - set labels critcal-{1-4} for those found bger cases
Error sources:
    - Regex cannot find correct file number in header
    - Regex found a wrong file
"""


class CriticalityDatasetCreator(DatasetCreator):
    """
    Creates a dataset containing bger cases and sets for each case a criticality label, based if the case was published
    as bge or not.
    """

    def __init__(self, config: dict, debug: bool = False):
        super().__init__(config, debug)
        self.logger = get_logger(__name__)
        self.split_type = "date-stratified"
        self.dataset_name = "criticality_prediction"
        self.feature_cols = [Section.FACTS, Section.CONSIDERATIONS, Section.RULINGS]
        self.reports_folder = self.create_dir(self.get_dataset_folder(), f'CH_BGer/reports')
        _, self.available_rulings_dict = self.load_rulings()
        self.references_df = self.extract_bge_references()
        self.labels = ['bge_label', 'citation_label']
        self.count_all_cits = False
        self.start_years[Split.TRAIN.value] = 2002
        self.additional_reports = True
        self.metadata = ['year', 'chamber', 'region',
                         'origin_chamber', 'origin_court', 'origin_canton',
                         'law_area']

    def extract_bge_references(self):
        """
        Extract data from bge_refereces_found.txt In order to do this bge_reference extractor must have run before.
        :return:        dataframe containing bger_references and bge_file_numbers
        """
        # TODO consider to write found reference into db instead of a file
        bge_references_file_path: Path = self.get_dataset_folder() / "bge_references_found.txt"
        if not bge_references_file_path.exists():
            raise Exception("bge references need to be extracted first. Run bge_reference_extractor.")
        df = pd.DataFrame({'bge_file_number_long': [], 'bge_file_number_short': [], 'bger_reference': [], 'year': [],
                           'bge_chamber': [], 'bger_chamber': [], 'law_area': []})
        with bge_references_file_path.open("r") as f:
            for line in f:
                (bge_file_number_long, bger_reference) = line.split()
                bger_chamber = bger_reference.split('_', 2)[0]
                law_area = get_legal_area_bger(bger_chamber)
                year = int(bge_file_number_long.split('-', 5)[1]) + 1874
                bge_chamber = bge_file_number_long.split('_', 5)[2]
                bge_file_number_short = bge_file_number_long.split('_')[3]
                s_row = pd.Series([bge_file_number_long, bge_file_number_short, bger_reference, year, bge_chamber, bger_chamber, law_area],
                                  index=df.columns)
                df = df.append(s_row, ignore_index=True)
        # Realize that decision_ids must be unique. But Filenames are not.
        self.logger.info(
            f"References_df: There are {len(df.index)} entries with {len(df.bge_file_number_long.unique())} unique bge_filenames and {len(df.bger_reference.unique())} unique bger_references.")
        # create some reports about the found references
        folder = self.create_dir(self.reports_folder, f'references')
        report_creator = ReportCreator(folder, self.debug)
        # report_creator.report_references(df)
        return df

    def prepare_dataset(self, save_reports, court_string):
        """
        get of all bger cases and set criticality labels: bge_label and citation_label
        :param save_reports:    whether or not to compute and save reports # TODO is False as default due to a bug
        :param court_string:    specifies court, in this case it is CH_BGer
        :return:                dataframe and list of labels
        """
        # We need file information to indentify found bge_references and citations to calculate amount of citations.
        data_to_load = {
            "section": True, "file": True, "file_number": True,
            "judgment": False, "citation": True, "lower_court": True,
            "law_area": True, "law_sub_area": True
        }
        df = self.get_df(self.get_engine(self.db_scrc), data_to_load)

        for feature_col in self.get_feature_col_names():
            tmp_df = df.rename(columns={f'{feature_col}_num_tokens_bert': "num_tokens_bert",
                                        f'{feature_col}_num_tokens_spacy': "num_tokens_spacy"})
            plot_input_length(self.reports_folder, tmp_df, feature_col)

        # bge criticality
        bge_list = self.get_bge_criticality_list()
        df['bge_label'] = "non-critical"
        df = self.set_critical_label(df, bge_list, 'bge_label')

        # enable this to get some additional reports

        if self.additional_reports:
            self.check_correctness_bge(df, bge_list)
            """
            # compare found references with bger citations
            bger_list = self.get_bger_citation_list()
            not_found_list = [item for item in bge_list if item not in bger_list]
            path = self.reports_folder / 'missing_bger_in_db.txt'
            with path.open("a") as f:
                for item in not_found_list:
                    f.write(f"{item}\n")
            """

        # citation criticality
        criticality_lists = self.get_citations_criticality_list(df.copy())
        df = self.set_multiple_labels(df, criticality_lists, 'citation_label')

        bge_labels, _ = list(np.unique(np.hstack(df.bge_label), return_index=True))
        citation_labels, _ = list(np.unique(np.hstack(df.citation_label), return_index=True))
        label_list = [bge_labels, citation_labels]
        dataset = datasets.Dataset.from_pandas(df)
        return dataset, label_list

    def set_critical_label(self, df, criticality_list, label):
        """
        Set label critical for cases in given citicality list, else set label non-critical
        :param df:                  dataframe of all bger cases
        :param criticality_list:    list of bger_file_numbers which will be labeled critical
        :param label:               name of label in df
        :return:                    labeled dataframe
        """
        file_number_match = df.file_number.astype(str).isin(list(criticality_list))
        df.loc[file_number_match, label] = 'critical'
        critical_df = df[df[label] == 'critical']
        self.logger.info(
            f"{label}: {len(critical_df.index)} critical cases and {len(critical_df.file_number.unique())} unique file_numbers.")
        return df

    def set_multiple_labels(self, df, critical_lists, label):
        """
        Create label with more than two classes
        :param df:                  dataframe of all bger cases
        :param criticality_lists:   list of lists of bger_file_numbers which will be labeled critical
        :param label:               name of label in df
        :return:                    labeled dataframe
        """
        df_list = []
        i = 1
        for critical_list in critical_lists:
            if critical_list:
                df = self.set_critical_label(df, critical_list, label)
                critical_df = df[df[label] == 'critical']
                df = df[df[label] != 'critical']
                # critical-1 = most important
                critical_df[label] = critical_df[label].map(
                    {'critical': f"critical-{i}"}, na_action=None)
                if not critical_df.empty:
                    df_list.append(critical_df)
            i = i + 1
        df[label] = 'non-critical'
        df_list.append(df)
        return pd.concat(df_list)

    def get_bge_criticality_list(self):
        """
        create list of all bger_references found in bge
        :return:    list of bger_file_numbers
        """
        self.logger.info(f"Processing labeling of bge_criticality")
        bge_ref_list = list(self.references_df['bger_reference'].unique())
        self.logger.info(f"There are {len(bge_ref_list)} references in the bge_criticality_list.")
        return bge_ref_list

    def get_bger_citation_list(self):
        """
        create list of all bger_references found in all bger with regex
        :return:    list of bger_file_numbers
        """
        df = self.extract_bger_citations()
        citations_lists = df['citations']
        citations_list = set([i for b in citations_lists for i in b])
        self.logger.info(f"Found {len(citations_list)} differenct citations.")
        return citations_list

    def get_citations_criticality_list(self, df):
        """
        create a list of lists of bger_references seperated depending how often they were cited
        :param df:                dataframe of all bger cases
        :return:                  list of lists of bger_references
        """
        self.logger.info(f"Processing labeling of citation_criticality")
        citations_df = self.process_data(df)

        folder = self.create_dir(self.reports_folder, f'citations')
        report_creator = ReportCreator(folder, self.debug)

        # report number of citations
        if self.additional_reports:
            report_creator.report_citations_count(citations_df, 'all')

        # separate cases into four classes using quartils of number of cits
        citations_scores = self.calculate_quartils(citations_df)

        critical_lists = []
        for i in range(len(citations_scores)):
            # bigger and not equal to avoid cases which were cited 0 times
            critical_df = citations_df[citations_df['counter'] > citations_scores[i]]
            critical_cases = list(critical_df['bger_reference'].unique())
            critical_lists.append(critical_cases)
            if self.additional_reports:
                report_creator.report_citations_count(citations_df, str(i))
        # the created lists are not disjunctive! cases with the highest citation_score are present in all lists
        return critical_lists

    def calculate_quartils(self, df):
        # get rid of entries where counter is 0
        df = df[df['counter'] > 0]
        stats = df['counter'].describe()
        cits = [stats['75%'], stats['50%'], stats['25%'], 0]
        return cits

    def process_citations(self, df):
        """
        process citations parallelized
        :param df:  dataframe of all bger cases
        :return:    dataframe with additional column with processed citations
        """
        df.dropna(subset=['citations'], inplace=True)
        df['ruling_citation'] = df.citations.apply(self.get_citation, type='ruling')
        df.dropna(subset=['ruling_citation'], inplace=True)
        return df

    def process_data(self, df):
        """
        Process data in df to get new df containing cases and a count of how often a case was cited
        :param df:      dataframe of all bger cases
        :return:        dataframe containing columns 'bger_reference', 'bge_file_number' and 'count'
        """
        df = self.parallelize_dataframe(df, self.process_citations)

        citation_frequencies_df = self.build_tf_matrix(df)

        # merge so we have counter in our reference df
        citation_count_df = self.references_df.copy()
        citation_count_df = pd.merge(citation_count_df, citation_frequencies_df, left_on='bge_file_number_short', right_on='file_number', how='left')

        # report citation count to be able to compare counts before and after weighting
        if self.additional_reports:
            citation_count_df.to_csv(self.reports_folder / f'citation_distribution_before_weighting.csv')
            folder = self.create_dir(self.reports_folder, 'before_weighting')
            report_creator = ReportCreator(folder, self.debug)
            report_creator.plot_two_attributes(citation_count_df, 'year', 'counter', 'all')
            report_creator.bin_plot_attribute(citation_count_df, 'counter', 'law_area', 0, 300, 10)

        # apply weight depending on year of cited case
        def apply_weight(row):
            if math.isnan(row['counter']):
                return 0
            weight = row['year'] - (self.start_years[Split.TRAIN.value]-1)
            if weight < 0:
                weight = 0
            return row['counter'] * weight / (self.current_year - (self.start_years[Split.TRAIN.value]-1))
        citation_count_df['counter'] = citation_count_df.apply(apply_weight, axis=1)

        return citation_count_df

    def build_tf_matrix(self, df):
        """
        Count for all found citations how often a case was cited
        """
        # count citations only once within one case
        df['ruling_citation'] = df['ruling_citation'].apply(lambda x: set(x))

        # create counter out of list
        df['ruling_citation'] = df['ruling_citation'].apply(lambda x: Counter(x))

        # create one counter for all citations
        type_corpus_frequency = Counter()
        for index, row in df.iterrows():
            type_corpus_frequency.update(row['ruling_citation'])

        citation_frequencies_df = pd.DataFrame.from_records(list(dict(type_corpus_frequency).items()),
                                                            columns=['file_number', 'counter'])
        return citation_frequencies_df

    def get_law_citation(self, citations_text):
        """
        We don't need law citations in criticality dataset creator
        """
        pass

    def plot_custom(self, report_creator, df, folder):
        """
        Saves statistics and reports about bge_label and citation_label. Specific for criticality_dataset_creator.
        :param report_creator:
        :param folder:          folder to save data
        :param df:              the df containing the dataset
        """
        for attribute in ['law_area', 'language']:
            report_creator.plot_attribute(df[df['bge_label'] == 'critical'], attribute, name='bge')

        report_creator.plot_label_ordered(df.rename(columns={'bge_label': 'label'}, inplace=False), 'bge_label')
        match = df['citation_label'] == 'non-critical'
        order_dict = dict(label=["citation_0", "citation_1", "citation_10", "citation_100"])
        if self.debug:
            report_creator.plot_label_ordered(df[~match].rename(columns={'citation_label': 'label'}, inplace=False),
                                              'citation_label')
        else:
            report_creator.plot_label_ordered(df[~match].rename(columns={'citation_label': 'label'}, inplace=False),
                                              'citation_label', order=order_dict)

    def check_correctness_bge(self, df, criticality_list):
        """
        to verify the correctness of our labels create some reports to compare bge_label with
        """
        critical_bge_df = df[df['bge_label'] == 'critical']
        # Check which file numbers could not been found for bge criticality
        labeled_critical_list = list(critical_bge_df.file_number.unique())
        not_found_list = [item for item in criticality_list if item not in labeled_critical_list]
        folder = self.create_dir(self.reports_folder, f'checks')
        report_creator = ReportCreator(folder, self.debug)
        report_creator.report_references_not_found(not_found_list, 'bge_label')
        report_creator.test_correctness_of_labeling(not_found_list, self.references_df)

    def extract_bger_citations(self):
        """
        To verify correctness of bge_label extract data from bger_citations_found.txt which contains all references
        found in all bger cases and not only in bge. This way we can make sure to have all bger_file_numbers, which
        should exist in our db.
        :return:        dataframe containing bger_citations and bge_file_numbers
        """
        self.logger.info("Extracting bger references")
        # get dict of bge references with corresponding bge file name
        bge_references_file_path: Path = self.get_dataset_folder() / "bger_citations_found.txt"
        if not bge_references_file_path.exists():
            raise Exception("bger citations need to be extracted first. Run bger_citations_extractor.")
        df = pd.DataFrame({'file_number_long': [], 'file_number_short': [], 'citations': []})
        with bge_references_file_path.open("r") as f:
            for line in f:
                if len(line.split(' ')) == 2:
                    (file_number_long, bger_citations) = line.split(' ')
                    citations = bger_citations.split('-')
                    file_number_short = file_number_long.split('_')[3]
                    s_row = pd.Series(
                        [file_number_long, file_number_short, citations], index=df.columns)
                    df = df.append(s_row, ignore_index=True)
                else:
                    pass
        return df

if __name__ == '__main__':
    config = get_config()
    criticality_dataset_creator = CriticalityDatasetCreator(config)
    criticality_dataset_creator.create_dataset(sub_datasets=False, kaggle=False, save_reports=True)
