import numpy as np

import scipy.stats
import os
import pandas as pd
import intervaltree 
from combat.pycombat import pycombat
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE
from matplotlib.patches import Patch
import re
from mass2chem.formula import calculate_mass, PROTON, ELECTRON, parse_chemformula_dict
import csv
from . import utils
from .utils import get_parser, get_similarity_method, search_for_mzml, file_operations
from functools import partial

class FeatureTable:
    qaqc_result_to_key = {
            "pca": "pca",
            "tsne": "tsne",
            "pearson_correlation": "pearson",
            "kendall_correlation": "kendall",
            "spearman_correlation": "spearman",
            "pearson_logtransformed_correlation": "log_pearson",
            "kendall_logtransformed_correlation": "log_kendall",
            "spearman_logtransformed_correlation": "log_spearman",
            "missing_feature_z_scores": "missing_feature_z_scores",
            "sum_intensity": "intensity_analysis",
            "mean_intensity": "intensity_analysis",
            "median_intensity": "intensity_analysis",
            "missing_dropped_sum_intensity": "intensity_analysis",
            "missing_dropped_mean_intensity": "intensity_analysis",
            "missing_dropped_median_intensity": "intensity_analysis",
            "log_missing_dropped_sum_intensity": "intensity_analysis",
            "log_missing_dropped_mean_intensity": "intensity_analysis",
            "log_missing_dropped_median_intensity": "intensity_analysis",
            "tics": "intensity_analysis",
            "log_tics": "intensity_analysis",
            "feature_count_z_scores": "feature_outlier_detection",
            "intensity_distribution": "intensity_distribution",
            "intensity_distribution_log": "intensity_distribution",
            "snr_distribution": "properties_distribution",
            "cSelectivity_distribution": "properties_distribution"
        }
    def __init__(self, feature_table, experiment, moniker):
        """
        This object wraps a feature table

        Args:
            feature_table_filepath (str): path to the feature table on disk
            experiment (Experiment object): the experiment object for this feature table
        """
        self.experiment = experiment
        self.feature_table = feature_table
        self.moniker = moniker

        self.clean_columns()
        self.__mz_trees = {}
        self.__rt_trees = {}

        self.method_map = {
            'pca': self.PCA,
            'tsne': self.TSNE,
            'pearson': partial(self.correlation_heatmap, correlation_type='pearson'),
            'kendall': partial(self.correlation_heatmap, correlation_type='kendall'),
            'spearman': partial(self.correlation_heatmap, correlation_type='spearman'),
            'log_pearson': partial(self.correlation_heatmap, correlation_type='pearson', log_transform=True),
            'log_kendall': partial(self.correlation_heatmap, correlation_type='kendall', log_transform=True),
            'log_spearman': partial(self.correlation_heatmap, correlation_type='spearman', log_transform=True),
            'missing_feature_percentiles': self.missing_feature_percentiles,
            'missing_feature_distribution': self.missing_feature_distribution,
            'missing_feature_z_scores': self.MissingFeatureZScores,
            'intensity_analysis': self.intensity_analysis,
            'feature_distribution': self.feature_distribution,
            'feature_outlier_detection': self.feature_distribution_outlier_detection,
            'intensity_distribution': self.intensity_distribution,
            'properties_distribution': self.properties_distribution
        }

        self.qaqc_result_to_method = {k: self.method_map[v] for k,v in self.qaqc_result_to_key.items()}

    def clean_columns(self):
        """
        Some helper scripts will convert the file path and append the 
        directory name on the sample names using '___' as a separator.

        This will convert these back to the anticipated names.
        """
        #_d = {os.path.basename(a.mzml_filepath).rstrip(".mzML") : a.name for a in self.experiment.acquisitions}
        if "cleaned" in self.moniker:
            return
        else:
            was_cleaned = False
            insert_count = 0
            to_drop = []
            for column in self.feature_table.columns:
                if '___' in column:
                    was_cleaned = True
                    insert_count += 1
                    self.feature_table[column.split('___')[-1]] = self.feature_table[column]
                    to_drop.append(column)
                    if insert_count == 10:
                        self.feature_table = self.feature_table.copy()
                        insert_count = 0
            if was_cleaned:
                if insert_count != 0:
                    self.feature_table = self.feature_table.copy()
                self.feature_table.drop(columns=to_drop, inplace=True)
                self.feature_table = self.feature_table.copy()
                self.save(new_moniker=self.moniker + "_cleaned")

    def get_mz_tree(self, mz_tol):
        """
        Construct an interval tree to search for features using a query
        mz and a specific mz tolerance in ppm.

        :param mz_tol: float or int, this is the mass resolution in ppm

        :return: interval tree for given mz_tol
        """
        if mz_tol not in self.__mz_trees:
            mz_tree = intervaltree.IntervalTree()
            for feature_id, mz in zip(self.feature_table["id_number"], self.feature_table["mz"]):
                mz_tree.addi(mz - mz / 1e6 * mz_tol, mz + mz / 1e6 * mz_tol, feature_id)
            self.__mz_trees[mz_tol] = mz_tree
        return self.__mz_trees[mz_tol]
    
    def get_rt_tree(self, rt_tol):
        """
        Construct an interval tree to search for features using a query
        rtime and a specific rtime tolerance in absolute units (sec).

        :param rt_tol: float or int, this is the rtime tolerance in sec

        :return: interval tree for given rt_tol
        """
        if rt_tol not in self.__rt_trees:
            rt_tree = intervaltree.IntervalTree()
            for feature_id, rtime in zip(self.feature_table["id_number"], self.feature_table["rtime"]):
                rt_tree.addi(rtime - rt_tol, rtime + rt_tol, feature_id)
            self.__rt_trees[rt_tol] = rt_tree
        return self.__rt_trees[rt_tol]

    @property
    def sample_columns(self):
        """sample_columns 

        Return a list of the column names in the feature table ƒthat are sample names.

        _extended_summary_

        This is used when filtering the feature tables. When we search the experiment for a set of samples with a given
        filter, this returns samples in the experiment that may not be in the feature table. We can use this list to 
        filter out the samples in the experiment not in the feature table. 

        :return: sample_columns
        :rtype: list
        """ 
        return [x for x in self.feature_table.columns if x.split("___")[-1] in self.experiment.sample_names]

    @property
    def non_sample_columns(self):
        """non_sample_columns 

        Return a list of the column names in the feature table that are sample names.

        This is used when filtering the feature tables but typically the list of sample columns is used instead.

        :return: non_sample_columns
        :rtype: list
        """        
        return [x for x in self.feature_table.columns if x not in self.sample_columns]

    @property
    def log_transformed(self):
        """log_transformed 

        This property queries the experiment object to determine if the feature table has been log transformed already

        Some operations log transform the feature table before analysis. Multiple log transforms would yield unwanted
        results so if an operation is going to log transform a feature table, check this first to ensure that it is 
        has not already been log transformed. 

        :return: is_log_transformed
        :rtype: boolean
        """        
        return self.moniker in self.experiment.log_transformed_feature_tables

    @property
    def num_features(self):
        """
        Returns the number of features in the feature table

        :return: integer, number of features in feature table
        """
        return self.feature_table.shape[0] - 1
    
    @property
    def num_samples(self):
        """
        Returns the number of samples in the feature table

        :return: integer, number of samples in feature table
        """
        return len(self.sample_columns)

    @staticmethod
    def load(moniker, experiment):
        """load 

        This method yields a FeatureTable object when given a feature table moniker. 

        _extended_summary_

        FeatureTables are registered with the experiment object using a moniker, a string that points to the file path
        for that feature table. This method queries the experiment object, gets the feature table path, and creates 
        the object. 

        :param moniker: the string with which the FeatureTable is registered
        :type moniker: string
        :param experiment: the experiment object with the FeatureTable
        :type experiment: object
        :return: the feature table for the moniker
        :rtype: FeatureTable
        """        
        moniker = moniker + "_cleaned" if moniker + "_cleaned" in experiment.feature_tables else moniker
        return FeatureTable(pd.read_csv(experiment.feature_tables[moniker], sep="\t"), experiment, moniker)

    def make_nonnegative(self, fill_value=1):
        """
        This replaces all NaN and 0 values in the feature table with the specified fill_value

        This is used primarially before log transforming the feature table to remove values that cannot be log transformed

        :param fill_value: the value to replace NaN and 0 with, defaults to 1
        :type fill_value: int, optional
        """        
        self.feature_table.fillna(0)
        for column in self.sample_columns:
            self.feature_table[column] = [x if x > 0 else fill_value for x in self.feature_table[column]]

    def save(self, new_moniker=None, drop_invariants=True):
        """
        Save the feature table as a pandas-created .tsv and register the new on-disk location with the experiment 
        object using the specified new_moniker or reuse the existing moniker. By default this drops features that 
        have no variance in the feature table. This can occur when a sample or samples are dropped and one or more
        features are zero or interpolated only in the remaining samples.

        When an operation is performed that modifies a feature table, the resulting feature table can be saved to 
        disk using this method. The moniker for the feature table can be reused or a new moniker provided. If a new
        moniker is provided it cannot be preferred or full since we do not want to overwrite the asari results. 

        Dropping invariants is recommended to reduce the size of the feature table and prevent uninformative features
        from reaching downstream steps. There is no good reason to turn it off, but the option exists. 

        :param new_moniker: a new moniker to register the saved table with the experiment object, defaults to None
        :type new_moniker: string, optional
        :param drop_invariants: if true, drop features that have no variance, defaults to True
        :type drop_invariants: bool, optional
        """
        if new_moniker is None:
            new_moniker = self.moniker
            if new_moniker == "preferred" or new_moniker == "full":
                raise Exception("Cannot overwrite asari feature tables") 

        if drop_invariants:
            self.drop_invariants()
        try:
            output_path = os.path.join(self.experiment.filtered_feature_tables_subdirectory, new_moniker + "_Feature_table.tsv")
            self.feature_table.to_csv(os.path.join(self.experiment.filtered_feature_tables_subdirectory, output_path), sep="\t", index=False)
            self.experiment.feature_tables[new_moniker] = output_path
            self.experiment.save()
            if os.path.exists(self.experiment.qaqc_figs + "/" + new_moniker):
                os.removedirs(self.experiment.qaqc_figs + "/" + new_moniker)
        except:
            print("FAILURE TO SAVE FEATURE TABLE")

    def save_fig_path(self, name):
        """save_fig_path 

        Given a desired name for a figure, this returns the path to which this figure should be saved.

        This ensures that the resulting path for the figure is a reasonable path without special figures and is 
        saved to the appropriate location in the experiment directory. 

        :param name: desired name for the figure
        :type name: string
        :return: path to save figure
        :rtype: string
        """        
        fig_path = os.path.join(os.path.abspath(self.experiment.qaqc_figs), self.moniker + "/")
        if not os.path.exists(fig_path):
            os.makedirs(fig_path)
        name = re.sub(r"[/\\?%*:|\"<>\x7F\x00-\x1F]", "_", name)
        return os.path.join(fig_path, self.experiment.experiment_name + "_" + name + ".png")

    def gen_figure(self, figure_type, data, title='', x_label=None, y_label=None, fig_params=None, skip_annot=False, bins=100):
        """gen_figure 

        #todo - this needs to be cleaned up.

        A single method is used to generate the figures for the FeatureTable. This allows for consistent looking
        figures to be generated. 

        The permitted types of figures are:

        "bar" - make a bar plot
        "scatter" - make a scatter plot
        "clustermap" - make a clustermap using seaborn
        "heatmap" - make a heatmap

        _extended_summary_

        :param figure_type: _description_
        :type figure_type: _type_
        :param data: _description_
        :type data: _type_
        :param title: the title for the figure, defaults to ''
        :type title: str, optional
        :param x_label: string to apply to the x-axis, defaults to None
        :type x_label: str, optional
        :param y_label: string to apply to the y-axis, defaults to None
        :type y_label: str, optional
        :param params: _description_, defaults to None
        :type params: _type_, optional
        :param skip_annot: if true do not apply cosmetics to the figure, defaults to False
        :type skip_annot: bool, optional
        """        
        import seaborn as sns
        import matplotlib.pyplot as plt
        import matplotlib.lines as mlines
        if fig_params is None:
            fig_params = self.figure_params

        if fig_params['interactive'] or fig_params['save_figs']:
            colors = fig_params['colors']
            markers = fig_params['markers']
            text = fig_params['text']
            if figure_type == "scatter":
                if type(data) is dict:
                    X = data.keys()
                    Y = data.values()
                else:
                    X = data[:, 0]
                    Y = data[:, 1]
                print(X)
                print(Y)
                plt.title(title)
                plt.xlabel(x_label)
                plt.ylabel(y_label)
                if skip_annot is False:
                    if markers and colors:
                        for x, y, c, m in zip(X, Y, list(colors[0]), list(markers[0])):
                            plt.scatter(x, y, c=c, marker=m)
                    elif markers and not colors:
                        for x, y, m in zip(X, Y, list(markers[0])):
                            plt.scatter(x, y, marker=m)
                    elif colors and not markers:
                        for x, y, c in zip(X, Y, list(colors[0])):
                            plt.scatter(x, y, c=c)
                    else:
                        plt.scatter(X, Y)
                    if text:
                        for x, y, t in zip(X, Y, text[0]):
                            plt.text(x, y, t)
                else:
                    plt.scatter(X, Y)
                if fig_params['marker_legend'] and skip_annot is False:
                    plt.tight_layout(rect=[0, 0, 0.75, 1])
                    handles = [mlines.Line2D(
                        [],
                        [],
                        color='k',
                        marker=v,
                        linestyle='None',
                        markersize=10,
                        label=k
                    ) for k, v in fig_params['marker_legend'].items() if v in markers[0]]
                    handles += [Patch(facecolor=v, label=k)
                                for k, v in fig_params['color_legend'].items() if v in colors[0]]
                    plt.legend(
                        handles=handles,
                        bbox_to_anchor=(1.0, 0.0),
                        bbox_transform=plt.gcf().transFigure,
                        loc='lower right'
                    )
            elif figure_type == "heatmap":
                if colors:
                    g = sns.clustermap(
                        data, col_colors=colors, yticklabels=y_label)
                else:
                    g = sns.clustermap(data, yticklabels=y_label)
                plt.suptitle(title)
                if fig_params['color_legend']:
                    plt.tight_layout(rect=[0, 0, 0.75, 1])
                    plt.legend(
                        [Patch(facecolor=color)
                         for color in fig_params['color_legend'].values()],
                        list(fig_params['color_legend'].keys()),
                        bbox_to_anchor=(1.0, 0.0),
                        bbox_transform=plt.gcf().transFigure,
                        loc='lower right'
                    )
            elif figure_type == "clustermap":
                if colors:
                    sns.clustermap(data, col_colors=colors)
                else:
                    sns.clustermap(data)
                plt.suptitle(title)
                if fig_params['color_legend']:
                    plt.tight_layout(rect=[0, 0, 0.75, 1])
                    plt.legend(
                        [Patch(facecolor=color)
                         for color in fig_params['color_legend'].values()],
                        list(fig_params['color_legend'].keys()),
                        bbox_to_anchor=(1.0, 0.0),
                        bbox_transform=plt.gcf().transFigure,
                        loc='lower right'
                    )
            elif figure_type == "bar":
                if type(data) is dict:
                    data = [list(data.keys()), list(data.values())]
                if text and colors:
                    plt.bar([x+"_"+str(i) for i, x in enumerate(text[0])], data[1], color=colors[0])
                elif text and not colors:
                    plt.bar([x+"_"+str(i) for i, x in enumerate(text[0])], data[1])
                elif not text and colors:
                    plt.bar([i for i in range(len(data[1]))], data[1], color=colors[0])
                else:
                    plt.bar([i for i in range(len(data[1]))], data[1])
                plt.title(title)
                plt.xticks(rotation=90)
                plt.xlabel(y_label)
                plt.ylabel(x_label)
                if fig_params['color_legend']:
                    plt.tight_layout(rect=[0, 0, 0.75, 1])
                    plt.legend(
                        [Patch(facecolor=color)
                         for color in fig_params['color_legend'].values()],
                        list(fig_params['color_legend'].keys()),
                        bbox_to_anchor=(1.0, 0.0),
                        bbox_transform=plt.gcf().transFigure,
                        loc='lower right'
                    )
            elif figure_type == "histogram":
                plt.suptitle(title)
                plt.hist(data, bins=bins)
            if fig_params['save_figs']:
                plt.savefig(self.save_fig_path(title.replace(" ", "_")))
            if fig_params['interactive']:
                plt.show()
            plt.clf()

    def search_for_feature(self, query_mz=None, query_rt=None, mz_tolerance=None, rt_tolerance=None):
        """search_for_feature 

        Given a query_mz and query_rt with corresponding tolerances in ppm and absolute units respectively find all 
        features by id_number that have a matching mz and rtime. 

        All search fields are optional but if none are provided then all the features will be considered matching. 
        The mz tolerance should be in ppm while the rtime tolerance should be provided in rtime units. 

        :param query_mz: the mz to search for, defaults to None
        :type query_mz: float, optional
        :param query_rt: the rtime to search for, defaults to None
        :type query_rt: float, optional
        :param mz_tolerance: the tolerance in ppm for the mz match, defaults to None
        :type mz_tolerance: float, optional
        :param rt_tolerance: the tolerance in absolute units for the rt match, defaults to None
        :type rt_tolerance: float, optional
        :return: list of matching feature IDs
        :rtype: list
        """        
        
        if query_mz and mz_tolerance:
            mz_matches = set([x.data for x in self.get_mz_tree(mz_tolerance).at(query_mz)])
            if query_rt is None or rt_tolerance is None:
                return mz_matches
        else:
            mz_matches = None
        if query_rt and rt_tolerance:
            rt_matches = set([x.data for x in self.get_rt_tree(rt_tolerance).at(query_rt)])
            if mz_matches is None or mz_tolerance is None:
                return rt_matches
        else:
            rt_matches = None
        return list(rt_matches.intersection(mz_matches))

    def intensity_distribution(self, skip_zero=True):
        if self.log_transformed:
            self.gen_figure(
                "histogram",
                [x for x in self.feature_table[self.sample_columns].values.flatten() if x and skip_zero],
                title="intensity_distribution_log",
                x_label="Intensity (Log-Transformed)",
                y_label="Counts"
            )
        else:
            self.gen_figure(
                "histogram",
                [x for x in self.feature_table[self.sample_columns].values.flatten() if x and skip_zero],
                title="intensity_distribution",
                x_label="Intensity",
                y_label="Counts"
            )
            self.gen_figure(
                "histogram",
                np.log2([x for x in self.feature_table[self.sample_columns].values.flatten() if x and skip_zero]),
                title="intensity_distribution_log",
                x_label="Intensity (Log-Transformed)",
                y_label="Counts"
            )

    def properties_distribution(self):
        for column in self.non_sample_columns:
            if column not in ["id_number", "parent_masstrack_id"]:
                print(column)
                try:
                    self.gen_figure(
                        "histogram",
                        self.feature_table[column].values.flatten(),
                        title=column + "_distribution",
                        x_label=column,
                        y_label="Counts",
                        bins=100
                    )
                except:
                    pass
                try:
                    self.gen_figure(
                        "histogram",
                        np.log10([x for x in self.feature_table[column].values.flatten() if x > 0]),
                        title="log10_" + column + "_distribution",
                        x_label=column,
                        y_label="Counts",
                        bins=100
                    )
                except:
                    pass

    
    def snr_distribution(self):
        self.properties_distribution()
    
    def cSelectivity_distribution(self):
        self.properties_distribution()

    def median_correlation_outlier_detection(self, correlation_type='pearson'):
        """
        The median correlation of a sample against all other samples can be expressed as a z-score against the median
        of ALL correlations in the experiment. A high or low Z-score indicates that the sample was poorly correlated 
        with other smaples in the experiment. 

        :param self: a feature table object
        :param figure_params: a dictionary specifying how to make figures
        :param correlation_type: can be 'pearson', 'spearman', 'kendall'

        :return: QAQC_result dict
        """
        correlation_result = self.correlation_heatmap(correlation_type=correlation_type)
        all_correlations = []
        median_correlations = {}
        for sample_name_1, corr_dict in correlation_result["Result"].items():
            correlation_for_sample_name_1 = []
            for sample_name_2, corr_value in corr_dict.items():
                if sample_name_1 != sample_name_2:
                    correlation_for_sample_name_1.append(corr_value)
            median_correlations[sample_name_1] = np.median(correlation_for_sample_name_1)
            all_correlations.extend(correlation_for_sample_name_1)
        all_correlations_std = np.std(all_correlations)
        all_correlations_median = np.median(all_correlations)
        z_score_correlations = {name: (median_correlation - all_correlations_median) /
                                all_correlations_std for name, median_correlation in median_correlations.items()}

        self.gen_figure(
            "scatter",
            {i: v for i, v in enumerate(median_correlations.values())},
            title="Median Correlation Values for Samples",
            x_label="Sample",
            y_label="Median Correlation Value",
        )
        self.gen_figure(
            "scatter",
            {i: v for i, v in enumerate(z_score_correlations.values())},
            title="Median Correlation Z-Scores for Samples",
            x_label="Sample",
            y_label="Median Correlation Z-Score",
        )

        result = {
            "Type": "MedianCorrelationZScores",
            "Config": {},
            "Result": z_score_correlations
        }
        return result

    def intensity_analysis(self):
        """
        This will report the sum, mean, median of features as well as
        those values when the missing values are removed or when they
        are log2 transformed. 

        :param self: a feature table object
        :param figure_params: a dictionary specifying how to make figures
        :param correlation_type: can be 'pearson', 'spearman', 'kendall'

        :return: QAQC_result dict
        """
        selected_ftable = self.feature_table[self.sample_columns].copy()
        intensity_sums = np.sum(selected_ftable, axis=0)
        mean_feature_intensity = np.mean(selected_ftable, axis=0)
        median_feature_intensity = np.median(selected_ftable, axis=0)

        selected_ftable = selected_ftable.copy()
        selected_ftable[selected_ftable == 0] = np.nan
        filtered_mean_feature_intensity = np.nanmean(selected_ftable, axis=0)
        filtered_median_feature_intensity = np.nanmedian(selected_ftable, axis=0)

        log_selected_ftable = np.log2(selected_ftable)
        log_filtered_intensity_sum = np.nansum(log_selected_ftable, axis=0)
        log_filtered_mean_feature_intensity = np.nanmean(log_selected_ftable, axis=0)
        log_filtered_median_feature_intensity = np.nanmedian(log_selected_ftable, axis=0)

        TICs = np.nansum(selected_ftable, axis=0)
        log_TICs = np.log2(TICs)

        tables = [intensity_sums, 
                  mean_feature_intensity, 
                  median_feature_intensity,
                  intensity_sums, 
                  filtered_mean_feature_intensity, 
                  filtered_median_feature_intensity,
                  log_filtered_intensity_sum, 
                  log_filtered_mean_feature_intensity, 
                  log_filtered_median_feature_intensity, 
                  log_TICs,
                  TICs]
        
        titles = ["sum_intensity", 
                  "mean_intensity", 
                  "median_intensity",
                  "missing_dropped_sum_intensity",
                  "missing_dropped_mean_intensity", 
                  "missing_dropped_median_intensity",
                  "log_missing_dropped_sum_intensity", 
                  "log_missing_dropped_mean_intensity", 
                  "log_missing_dropped_median_intensity",
                  "log_tics",
                  "tics"]

        for table, title in zip(tables, titles):
            results = {k: v for k, v in zip(self.sample_columns, table)}

            self.gen_figure(
                "bar",
                results,
                title,
                x_label="title",
                y_label="sample",
            )
            #except:
            print("blah blah")

        result_values = {
                "sum_intensity": {name: value for name, value in zip(self.sample_columns, intensity_sums)},
                "mean_intensity": {name: value for name, value in zip(self.sample_columns, mean_feature_intensity)},
                "median_intensity": {name: value for name, value in zip(self.sample_columns, median_feature_intensity)},
                "missing_dropped_sum_intensity": {name: value for name, value in zip(self.sample_columns, intensity_sums)},
                "missing_dropped_mean_intensity": {name: value for name, value in zip(self.sample_columns, filtered_mean_feature_intensity)},
                "missing_dropped_median_intensity": {name: value for name, value in zip(self.sample_columns, filtered_median_feature_intensity)},
                "log_missing_dropped_sum_intensity": {name: value for name, value in zip(self.sample_columns, log_filtered_intensity_sum)},
                "log_missing_dropped_mean_intensity": {name: value for name, value in zip(self.sample_columns, log_filtered_mean_feature_intensity)},
                "log_missing_dropped_median_intensity": {name: value for name, value in zip(self.sample_columns, log_filtered_median_feature_intensity)},
                "log_tics": {name: value for name, value in zip(self.sample_columns, log_TICs)},
                "tics": {name: value for name, value in zip(self.sample_columns, TICs)}
            }
        results = []
        for k, v in result_values.items():
            results.append(
                {
                    "Type": k,
                    "Config": {},
                    "Result": v
                }
            )
        return results

    def correlation_heatmap(self, correlation_type, log_transform=False):
        """correlation_heatmap 

        Using a specified correlation function generate a correlation heatmap for the feature table. Optionally,
        log transform the feature table first. 

        The permitted correlation types are: 

        "pearson", "spearman" or "kendall"

        Only pearson will log_transform the feature table if enabled since the non-parametric correlations will not 
        be affected by the log transform. 

        :param figure_params: dictionary with the figure params
        :type figure_params: dict
        :param correlation_type: what correlation type to use
        :type correlation_type: str
        :param log_transform: if true, log transform before linear correlation, defaults to True
        :type log_transform: bool, optional
        :return: a dictionary with the correlation results and configuration used to generate the result
        :rtype: dict
        """        
        corr_method = utils.correlation_modes[correlation_type]
        corr_matrix = np.zeros((self.num_samples, self.num_samples))
        working_table = self.feature_table.copy()
        if log_transform:
            working_table = np.log2(working_table[self.sample_columns] + 1)
        for i, s1 in enumerate(self.sample_columns):
            val_s1 = working_table[s1]
            for j, s2 in enumerate(self.sample_columns):
                if corr_matrix[j][i] != 0:
                    corr_matrix[i][j] = corr_matrix[j][i]
                else:
                    corr = corr_method(val_s1, working_table[s2])
                    try:
                        corr_matrix[i][j] = corr.statistic
                    except:
                        corr_matrix[i][j] = corr[0][1]


        if log_transform:
            title = correlation_type + "_logtransformed_correlation"
        else:
            title = correlation_type + "_correlation"

        self.gen_figure(
            "clustermap",
            corr_matrix,
            title=title,
            x_label=self.figure_params['text'],
            y_label=self.figure_params['text'],
        )
        result = {
            "Type": title,
            "Config": {"Metric": correlation_type, "LogTransformed": log_transform},
            "Result": {self.sample_columns[i]: {self.sample_columns[j]: float(corr_matrix[i][j]) for j in range(corr_matrix.shape[0])} for i in range(corr_matrix.shape[0])}
        }
        return result

    def PCA(self, log_transform=True):
        """
        Perform PCA on provided feature table, optionally log transform
        it first. 

        :param figure_params: dictionary with figure params

        :return: QAQC_result dict
        """
        sample_ftable = self.feature_table[self.sample_columns].T.copy()
        scaler = StandardScaler()
        pca_embedder = PCA(n_components=2)
        if log_transform and not self.log_transformed:
            sample_ftable = np.log2(sample_ftable+1)
        pca_embedding = pca_embedder.fit_transform(scaler.fit_transform((sample_ftable)))
        self.gen_figure("scatter",
                        pca_embedding,
                        "pca",
                        x_label="PC 1 " +
                        str(round(pca_embedder.explained_variance_ratio_[0] * 100, 1)) + "%",
                        y_label="PC 2 " +
                        str(round(pca_embedder.explained_variance_ratio_[1] * 100, 1)) + "%",
                        )
        result = {
            "Type": "pca",
            "Config": {"n_components": 2, "scaler": "StandardScaler"},
            "Result": {"Sample_Coord_Dict": {name: list(coord) for name, coord in zip(self.sample_columns, pca_embedding)}}
        }
        return result

    def TSNE(self, perplexity=30):
        """
        Perform TSNE on provided feature table

        :param figure_params: dictionary for the figure generation 
        :param perplexity: perplexity value for TSNE

        :result: QAQC result dict
        """
        try:
            tnse_embedded_vector_matrix = TSNE(n_components=2, perplexity=perplexity).fit_transform(self.feature_table[self.sample_columns].T)
            self.gen_figure(
                "scatter",
                tnse_embedded_vector_matrix,
                "tsne",
                x_label="Latent 1",
                y_label="Latent 2",
            )
            result = {
                "Type": "tsne",
                "Config": {"n_components": 2},
                "Result": {"Sample_Coord_Dict": {name: [float(x) for x in coord] for name, coord in zip(self.sample_columns, tnse_embedded_vector_matrix)}}
            }
            return result
        except:
            if perplexity > 0:
                self.TSNE(perplexity=perplexity-1)
            else:
                return {}

    def missing_feature_percentiles(self):
        """
        Calculate the distribution of missing features with respect to percent of smaples with feature

        Args:
            feature_vector_matrix (np.ndarray): the selected feature matrix
            interactive_plot (bool, optional): if True, interactive plots are made. Defaults to False.

        Returns:
            result: dictionary storing the result of this QCQA operation
        """
        def __count_feature(row, columns):
            return np.sum([1 for x in row[columns] if x > 0])

        num_sample_with_feature = self.feature_table.apply(__count_feature, axis=1, args=(self.sample_columns,))
        percentile_table = []
        for percentile in range(101):
            num_samples_threshold = len(self.sample_columns) * percentile/100
            percentile_table.append([percentile, num_samples_threshold, int(np.sum(num_sample_with_feature <= num_samples_threshold))])
        self.gen_figure(
            "scatter",
            np.array([[x[0], x[2]] for x in percentile_table]),
            title="Missing Feature Percentiles",
            x_label="Percentile",
            y_label="Num. Dropped Features",
            skip_annot=True
        )
        result = {
            "Type": "missingfeaturepercentiles",
            "Config": {},
            "Result": {"PercentileTable": percentile_table}
        }
        return result

    def missing_feature_distribution(self, intensity_cutoff=0):
        """
        Count the number of missing features or featuers below the specified intensity cutoff per features

        Args:
            feature_vector_matrix (np.ndarray): the selected feature matrix
            acquisition_names (list[str]): list of acquisition names
            intensity_cutoff (int, optional): values below this intesnity are considered missing. Defaults to 0.
            interactive_plot (bool, optional): if True, interactive plots are made. Defaults to False.

        Returns:
            result: dictionary storing the result of this QCQA operation
        """
        masked_ftables = self.feature_table[self.sample_columns] <= intensity_cutoff
        missing_feature_counts = dict(zip(self.sample_columns, [0 for _ in self.sample_columns]))
        for name in self.sample_columns:
            for value in masked_ftables[name]:
                if value is True:
                    missing_feature_counts[name] += 1
        self.gen_figure(
            "bar",
            (self.sample_columns, [missing_feature_counts[name] for name in self.sample_columns]),
            title="missing_feature_counts",
            x_label="Missing Feature Counts",
            y_label="Num. Missing Features",
        )
        result = {
            "Type": "MissingFeatureDistribution",
            "Config": {"intensity_cutoff": intensity_cutoff},
            "Result": {name: int(num_missing) for name, num_missing in missing_feature_counts.items()}
        }
        return result

    def feature_distribution(self, intensity_cutoff=0):
        """
        Count the number of features above the specified intensity cutoff per features

        Args:
            feature_vector_matrix (np.ndarray): the selected feature matrix
            acquisition_names (list[str]): list of acquisition names
            intensity_cutoff (int, optional): values above this intensity are considered. Defaults to 0.
            interactive_plot (bool, optional): if True, interactive plots are made. Defaults to False.

        Returns:
            result: dictionary storing the result of this QCQA operation
        """
        masked_ftables = self.feature_table[self.sample_columns] > intensity_cutoff
        feature_counts = dict(zip(self.sample_columns, [0 for _ in self.sample_columns]))
        for name in self.sample_columns:
            for value in masked_ftables[name]:
                if value is True:
                    feature_counts[name] += 1
        self.gen_figure(
            "bar",
            (self.sample_columns, [feature_counts[name]
             for name in self.sample_columns]),
            title="Feature Counts",
            y_label="Num. Features",
        )
        result = {
            "Type": "FeatureDistribution",
            "Config": {"intensity_cutoff": intensity_cutoff},
            "Result": {name: int(num_missing) for name, num_missing in feature_counts.items()}
        }
        return result

    def feature_distribution_outlier_detection(self, intensity_cutoff=0):
        """
        Count the number of features above the specified intensity cutoff per features and express as a Z-score based
        on feature count across all samples. 

        Args:
            feature_vector_matrix (np.ndarray): the selected feature matrix
            acquisition_names (list[str]): list of acquisition names
            intensity_cutoff (int, optional): values above this intensity are considered. Defaults to 0.
            interactive_plot (bool, optional): if True, interactive plots are made. Defaults to False.

        Returns:
            result: dictionary storing the result of this QCQA operation
        """
        feature_counts_result = self.feature_distribution(intensity_cutoff=intensity_cutoff)
        feature_counts = np.array([*feature_counts_result["Result"].values()])
        feature_z_scores = (feature_counts - np.mean(feature_counts)) / np.std(feature_counts)
        self.gen_figure(
            "scatter",
            {i: z_score for i, z_score in enumerate(feature_z_scores)},
            title="feature_count_z_scores",
            x_label="Sample",
            y_label="Num Feature Z-Score",
        )
        result = {
            "Type": "feature_count_z_scores",
            "Config": {"intensity_cutoff": intensity_cutoff},
            "Result": {name: float(z_score) for name, z_score in zip(self.sample_columns, feature_z_scores)}
        }
        return result

    def MissingFeatureZScores(self, intensity_cutoff=0):
        """
        Count the number of features below the specified intensity cutoff per features and express as a Z-score based
        on missing feature count across all samples. 

        Args:
            feature_vector_matrix (np.ndarray): the selected feature matrix
            acquisition_names (list[str]): list of acquisition names
            intensity_cutoff (int, optional): values below this intensity are considered missing. Defaults to 0.
            interactive_plot (bool, optional): if True, interactive plots are made. Defaults to False.

        Returns:
            result: dictionary storing the result of this QCQA operation
        """
        missing_feature_counts_result = self.missing_feature_distribution(intensity_cutoff=intensity_cutoff)
        # this relies upon the sorted order of the dictionary, may not be safe in all Python versions
        sample_names = [*missing_feature_counts_result["Result"].keys()]
        missing_feature_counts = np.array([*missing_feature_counts_result["Result"].values()])
        missing_feature_count_std = np.std(missing_feature_counts)


        missing_feature_z_scores = (missing_feature_counts - np.mean(missing_feature_counts)) / np.std(missing_feature_counts)
        self.gen_figure(
            "scatter",
            {i: z_score for i, z_score in enumerate(missing_feature_z_scores)},
            title="missing_feature_z_scores",
            x_label="Sample",
            y_label="Num Missing Feature Z-Score",
        )
        result = {
            "Type": "missing_feature_z_scores",
            "Config": {"intensity_cutoff": intensity_cutoff},
            "Result": {name: float(z_score) for name, z_score in zip(sample_names, missing_feature_z_scores)}
        }
        return result

    def drop_invariants(self, zeros_only=False):
        """
        This method drops features that have all zero intensity or the same intensity across all samples.

        This situation occurs as a result of filtering. For instance if a contaiminant is only seen in the blanks, 
        when the blanks are dropped from the feature table, that feature is still in the table but will be zero (or an
        interpolated value) for the remaning samples. These features have no information and can complicate downstream
        analysis. 

        :param zeros_only: if true, only drop features that are all zero, defaults to False
        :type zeros_only: bool, optional
        """        
        def __filter_invariant(row, columns):
            values = set()
            for column in columns:
                if column in row:
                    values.add(row[column])
            if len(values) == 1:
                if zeros_only and values[0] == 0:
                    return False
                else:
                    return False
            return True

        to_keep = []

        for keep_feature, id_number in zip(self.feature_table.apply(__filter_invariant, axis=1, args=(self.sample_columns,)), self.feature_table["id_number"]):
            if keep_feature:
                to_keep.append(id_number)
        self.feature_table = self.feature_table[self.feature_table['id_number'].isin(to_keep)].copy()

        for sample_column in self.sample_columns:
            values = set(list([x for x in self.feature_table[sample_column]]))
            if len(values) == 1:
                if zeros_only and values[0] == 0:
                    self.feature_table.drop(
                        columns=[sample_column], inplace=True)
                else:
                    self.feature_table.drop(
                        columns=[sample_column], inplace=True)

    def drop_sample_by_name(self, drop_name, drop_others=False):
        if drop_others:
            self.feature_table.drop(columns=[x for x in self.sample_columns if x != drop_name], inplace=True)
        else:
            self.feature_table.drop(columns=drop_name, inplace=True)

    def drop_samples_by_filter(self, filter, drop_others=False):
        to_drop = [acq.name for acq in self.experiment.filter_samples(filter)]
        to_drop = [x for x in to_drop if x in self.sample_columns]
        do_not_drop = [x for x in self.sample_columns if x not in to_drop]
        if drop_others:
            to_drop, do_not_drop = do_not_drop, to_drop
        self.feature_table.drop(columns=to_drop, inplace=True)

    def drop_samples_by_field(self, value, field, drop_others=False):
        self.drop_samples_by_filter({field: {"includes": [value]}}, drop_others=drop_others)

    def drop_samples_by_qaqc(self, qaqc_filter, drop_others=False, params=None):
        to_drop = []
        for field in qaqc_filter.keys():
            if ">" in qaqc_filter[field]["Conditions"]:
                max_value = float(qaqc_filter[field]["Conditions"][">"])
            else:
                max_value = np.inf
            if "<" in qaqc_filter[field]["Conditions"]:
                min_value = float(qaqc_filter[field]["Conditions"]["<"])
            else:
                min_value = -np.inf
            action = qaqc_filter[field]["Action"]
            if self.moniker not in self.experiment.qcqa_results:
                self.experiment.qcqa_results[self.moniker] = {}
            if field not in self.experiment.qcqa_results[self.moniker] and params:
                method = self.qaqc_result_to_method.get(field, None)
                self.figure_params = {}
                self.figure_params["interactive"] = self.figure_params["save_figs"] = False
                if method:
                    result = method()
                    if type(result) is dict:
                        result = [result]
                    for qaqc_result in result:
                        self.experiment.qcqa_results[self.moniker][qaqc_result["Type"]] = qaqc_result
                else:
                    print("No method found for " + field)
            qaqc_results_for_field = self.experiment.qcqa_results[self.moniker].get(field, None)
            if qaqc_results_for_field:
                for sample, value in qaqc_results_for_field["Result"].items():
                    if not (min_value < float(value) < max_value):
                        if action == "Keep":
                            pass
                        elif action == "Drop":
                            to_drop.append(sample)
            else:
                print("No qaqc results found for " + field)
        to_drop = [x for x in to_drop if x in self.sample_columns]
        if drop_others:
            to_drop = [x for x in self.sample_columns if x not in to_drop]
        if to_drop:
            print("Dropping: \n\t" + "\t\n".join(to_drop))
            self.feature_table.drop(columns=to_drop, inplace=True)

    def blank_mask(self, 
                   blank_value="Blank",
                   sample_value="Unknown",
                   query_field="Sample Type",
                   filter=None,
                   blank_intensity_ratio=3,
                   by_batch=None,
                   logic_mode="or"):
        """blank_mask 

        Given a feature table containing samples that we consider blanks, drop all features in non-blank samples that
        do not have an intensity blank_intensity_ratio times higher than the mean intensity in the blanks.

        The blank samples are specified by the comibnation of blank_type and type_field. Non-blank samples are 
        specified by sample_type and type_field in a similar manner. 

        If there are batches in the experiment, blank masking is done per-batch. Then dropped if the ratio condition is
        not true in one sample (if logic_mode is "or") or in all samples if logic_mode is "and". The batches are 
        specified given a field in the metadata via the by_batch field.

        _extended_summary_

        :param new_moniker: _description_
        :type new_moniker: _type_
        :param by_batch: _description_, defaults to None
        :type by_batch: _type_, optional
        :param blank_intensity_ratio: _description_, defaults to 3
        :type blank_intensity_ratio: int, optional
        :param logic_mode: _description_, defaults to "or"
        :type logic_mode: str, optional
        :param blank_type: _description_, defaults to "Blank"
        :type blank_type: str, optional
        :param sample_type: _description_, defaults to "Unknown"
        :type sample_type: str, optional
        :param type_field: _description_, defaults to "Sample Type"
        :type type_field: str, optional
        """

        def __non_zero_mean(row, columns):
            non_zero_columns = [x for x in row[columns] if x > 0]
            return np.mean(non_zero_columns) if len(non_zero_columns) > 0 else 0

        def __any_logical(row, columns):
            return np.any(row[columns] == True)

        def __all_logical(row, columns):
            return np.all(row[columns] == True)

        blank_names = [x.name for x in self.experiment.filter_samples({query_field: {"includes": [blank_value]}}) if x.name in self.sample_columns]        
        sample_names = [x.name for x in self.experiment.filter_samples({query_field: {"includes": [sample_value]}}) if x.name in self.sample_columns]
        
        blank_mask_columns = []
        if by_batch:
            for batch_name, batch_name_list in self.experiment.batches(by_batch).items():
                batch_blanks = [x for x in batch_name_list if x in blank_names]
                batch_samples = [
                    x for x in batch_name_list if x in sample_names]
                blank_means = self.feature_table.apply(__non_zero_mean, axis=1, args=(batch_blanks,))
                sample_means = self.feature_table.apply(__non_zero_mean, axis=1, args=(batch_samples,))
                to_filter = []
                for blank_mean, sample_mean in zip(blank_means, sample_means):
                    to_filter.append(blank_mean * blank_intensity_ratio > sample_mean)
                    print("batch", blank_mean, sample_mean, blank_mean * blank_intensity_ratio > sample_mean)
                blank_mask_column = "blank_masked_" + batch_name
                blank_mask_columns.append(blank_mask_column)
                self.feature_table[blank_mask_column] = to_filter
            if logic_mode == "and":
                self.feature_table["mask_feature"] = self.feature_table.apply(__all_logical, axis=1, args=(blank_mask_columns,))
            elif logic_mode == "or":
                self.feature_table["mask_feature"] = self.feature_table.apply(__any_logical, axis=1, args=(blank_mask_columns,))
            for blank_mask_column in blank_mask_columns:
                self.feature_table.drop(columns=blank_mask_column, inplace=True)
        else:
            blank_means = self.feature_table.apply(__non_zero_mean, axis=1, args=(list(blank_names),))
            sample_means = self.feature_table.apply(__non_zero_mean, axis=1, args=(list(sample_names),))
            to_filter = []
            for blank_mean, sample_mean in zip(blank_means, sample_means):
                to_filter.append(blank_mean * blank_intensity_ratio > sample_mean)
            blank_mask_column = "mask_feature"
            self.feature_table["mask_feature"] = to_filter
        self.feature_table = self.feature_table[self.feature_table["mask_feature"] == False]
        self.feature_table.drop(columns="mask_feature", inplace=True)

    def interpolate_missing_features(self, ratio=0.5, by_batch=None, method="min"):
        """interpolate_missing_features _summary_

        _extended_summary_

        :param new_moniker: _description_
        :type new_moniker: _type_
        :param ratio: _description_, defaults to 0.5
        :type ratio: float, optional
        :param by_batch: _description_, defaults to None
        :type by_batch: _type_, optional
        """        

        def calc_interpolated_value(row, sample_names):
            values = [x for x in row[sample_names] if x > 0]
            if values:
                return utils.descriptive_stat_modes[method](values) * ratio
            else:
                return 0

        sample_names = [a.name for a in self.experiment.acquisitions if a.name in self.feature_table.columns]
        if by_batch:
            for _, batch_name_list in self.experiment.batches(by_batch).items():
                filtered_batch_name_list = [
                    x for x in batch_name_list if x in sample_names]
                self.feature_table["feature_interpolate_value"] = self.feature_table.apply(calc_interpolated_value, axis=1, args=(filtered_batch_name_list,))
                for sample_name in filtered_batch_name_list:
                    self.feature_table[sample_name] = self.feature_table[[sample_name, "feature_interpolate_value"]].max(axis=1)
                self.feature_table.drop(columns="feature_interpolate_value", inplace=True)
        else:
            self.feature_table["feature_interpolate_value"] = self.feature_table.apply(calc_interpolated_value, axis=1, args=(sample_names,))
            for sample_name in sample_names:
                self.feature_table[sample_name] = self.feature_table[[sample_name, "feature_interpolate_value"]].max(axis=1)
            self.feature_table.drop(columns="feature_interpolate_value", inplace=True)

    def TIC_normalize(self, TIC_normalization_percentile=0.90, by_batch=None, normalize_mode='median'):
        """TIC_normalize _summary_

        _extended_summary_

        :param new_moniker: _description_
        :type new_moniker: _type_
        :param TIC_normalization_percentile: _descr iption_, defaults to 0.90
        :type TIC_normalization_percentile: float, optional
        :param by_batch: _description_, defaults to None
        :type by_batch: _type_, optional
        :param sample_type: _description_, defaults to "Unknown"
        :type sample_type: str, optional
        :param type_field: _description_, defaults to "Sample Type"
        :type type_field: str, optional
        :param normalize_mode: _description_, defaults to 'median'
        :type normalize_mode: str, optional
        """        

        if by_batch is not None:
            aggregate_batch_TICs = {}
            for batch_name, batch_name_list in self.experiment.batches(by_batch).items():
                print("Batching")
                batch_name_list = [x for x in batch_name_list if x in self.feature_table.columns]
                self.feature_table["percent_inclusion"] = np.sum(self.feature_table[batch_name_list] > 0, axis=1) / len(batch_name_list)
                TICs = {sample: np.sum(self.feature_table[self.feature_table["percent_inclusion"] > TIC_normalization_percentile][sample]) for sample in batch_name_list}
                norm_factors = {sample: utils.descriptive_stat_modes[normalize_mode](list(TICs.values()))/value for sample, value in TICs.items()}
                aggregate_batch_TICs[batch_name] = utils.descriptive_stat_modes[normalize_mode](list(TICs.values()))
                for sample, norm_factor in norm_factors.items():
                    self.feature_table[sample] = self.feature_table[sample] * norm_factor
            aggregate_batch_TIC_corrections = {batch: utils.descriptive_stat_modes[normalize_mode](list(aggregate_batch_TICs.values()))/value for batch, value in aggregate_batch_TICs.items()}
            for batch_name, batch_name_list in self.experiment.batches(by_batch).items():
                batch_name_list = [x for x in batch_name_list if x in self.feature_table.columns]
                for sample in batch_name_list:
                    self.feature_table[sample] = self.feature_table[sample] * aggregate_batch_TIC_corrections[batch_name]
        else:
            sample_names = [x for x in self.feature_table.columns if x in [a.name for a in self.experiment.acquisitions]]
            self.feature_table["percent_inclusion"] = np.sum(self.feature_table[sample_names] > 0, axis=1) / len(sample_names)
            TICs = {sample: np.sum(self.feature_table[self.feature_table["percent_inclusion"] > TIC_normalization_percentile][sample]) for sample in sample_names}
            norm_factors = {sample: utils.descriptive_stat_modes[normalize_mode](
                list(TICs.values()))/value for sample, value in TICs.items()}
            for sample, norm_factor in norm_factors.items():
                self.feature_table[sample] = self.feature_table[sample] * norm_factor
        self.feature_table.drop(columns="percent_inclusion", inplace=True)

    def batch_correct(self, by_batch):
        """batch_correct _summary_

        _extended_summary_

        :param new_moniker: _description_
        :type new_moniker: _type_
        :param by_batch: _description_
        :type by_batch: _type_
        """        
        if len(self.experiment.batches(by_batch).keys()) > 1:
            batch_idx_map = {}
            for batch_idx, (_, acquisition_list) in enumerate(self.experiment.batches(by_batch).items()):
                for acquisition in acquisition_list:
                    batch_idx_map[acquisition] = batch_idx
            batches = [batch_idx_map[x] for x in self.sample_columns]
            #batches = [1 if x < 8 else 2 for x in batches]
            batch_corrected = pycombat(self.feature_table[self.sample_columns], batches)
            for column in batch_corrected.columns:
                self.feature_table[column] = batch_corrected[column]
            self.make_nonnegative(fill_value=1)
        else:
            print("Unable to batch correct if only one batch!")
            raise Exception()

    def log_transform(self, new_moniker, log_mode="log2"):
        """log_transform _summary_

        _extended_summary_

        :param new_moniker: _description_
        :type new_moniker: _type_
        :param log_mode: _description_, defaults to "log2"
        :type log_mode: str, optional
        """
        try:
            self.experiment.log_transformed_feature_tables.append(new_moniker)
            self.experiment.save()
        except:
            self.experiment.log_transformed_feature_tables = [new_moniker]
            self.experiment.save()

        for sample_name in self.sample_columns:
            self.feature_table[sample_name] = utils.log_modes[log_mode](self.feature_table[sample_name]+1)
        self.make_nonnegative()

    def drop_missing_features(self, by_batch=None, drop_percentile=0.8, logic_mode="or"):
        """drop_missing_features _summary_

        _extended_summary_

        :param new_moniker: _description_
        :type new_moniker: _type_
        :param by_batch: _description_, defaults to None
        :type by_batch: _type_, optional
        :param drop_percentile: _description_, defaults to 0.8
        :type drop_percentile: float, optional
        :param logic_mode: _description_, defaults to "or"
        :type logic_mode: str, optional
        :param sample_type: _description_, defaults to "Unknown"
        :type sample_type: str, optional
        :param type_field: _description_, defaults to "Sample Type"
        :type type_field: str, optional
        """        
        def __any(row, columns, drop_percentile):
            return not np.any(row[columns] >= drop_percentile)

        def __all(row, columns, drop_percentile):
            return not np.all(row[columns] >= drop_percentile)

        batch_columns = []
        if by_batch:
            for batch_name, batch_name_list in self.experiment.batches(by_batch).items():
                batch_column = "percent_inclusion_" + batch_name
                filtered_batch_name_list = [x for x in batch_name_list if x in self.sample_columns]
                self.feature_table[batch_column] = np.sum(self.feature_table[filtered_batch_name_list] > 0, axis=1) / len(filtered_batch_name_list)
                batch_columns.append(batch_column)
            if logic_mode == "and":
                self.feature_table["drop_feature"] = self.feature_table.apply(__all, axis=1, args=(batch_columns, drop_percentile))
            elif logic_mode == "or":
                self.feature_table["drop_feature"] = self.feature_table.apply(__any, axis=1, args=(batch_columns, drop_percentile))
        else:
            self.feature_table["drop_feature"] = (np.sum(self.feature_table[self.sample_columns] > 0, axis=1) / len(self.sample_columns)) < drop_percentile
        self.feature_table = self.feature_table[self.feature_table["drop_feature"] == False]
        self.feature_table.drop(columns="drop_feature", inplace=True)

    def generate_cosmetic(self, colorby=None, markerby=None, textby=None, seed=None):
        """generate_cosmetic _summary_

        _extended_summary_

        :param colorby: _description_, defaults to None
        :type colorby: _type_, optional
        :param markerby: _description_, defaults to None
        :type markerby: _type_, optional
        :param textby: _description_, defaults to None
        :type textby: _type_, optional
        :param seed: _description_, defaults to None
        :type seed: _type_, optional
        :return: _description_
        :rtype: _type_
        """        
        combined_cosmetic_map = {}
        if colorby:
            for cosmetic_map in [self.experiment.generate_cosmetic_map(c, 'color', seed) for c in colorby]:
                if cosmetic_map:
                    for k, v in cosmetic_map.items():
                        combined_cosmetic_map[("color", k)] = v
        if markerby:
            for cosmetic_map in [self.experiment.generate_cosmetic_map(m, 'marker', seed) for m in markerby]:
                if cosmetic_map:
                    for k, v in cosmetic_map.items():
                        combined_cosmetic_map[("marker", k)] = v
        colors = [[] for _ in colorby]
        markers = [[] for _ in markerby]
        texts = [[] for _ in textby]
        color_legend = {}
        marker_legend = {}
        for sample_name in self.sample_columns:
            sample_name = sample_name.split('___')[-1]
            for acquisition in self.experiment.acquisitions:
                if acquisition.name == sample_name:
                    for i, x in enumerate(colorby):
                        colors[i].append(combined_cosmetic_map[('color', acquisition.metadata_tags[x])])
                        color_legend[acquisition.metadata_tags[x]] = combined_cosmetic_map[('color', acquisition.metadata_tags[x])]
                    for i, x in enumerate(markerby):
                        markers[i].append(combined_cosmetic_map[('marker', acquisition.metadata_tags[x])])
                        marker_legend[acquisition.metadata_tags[x]] = combined_cosmetic_map[('marker', acquisition.metadata_tags[x])]
                    for i, x in enumerate(textby):
                        texts[i].append(acquisition.metadata_tags[x])
                    break
        return colors, markers, texts, color_legend, marker_legend


    def generate_figure_params(self, params):
        import json
        for x in ['color_by', 'marker_by', 'text_by']:
            if x in params and type(params[x]) is str:
                params[x] = json.loads(params[x])
        colors, markers, texts, color_legend, marker_legend = self.generate_cosmetic(params['color_by'], params['marker_by'], params['text_by'], params['seed'])
        self.figure_params = {
            "acquisitions": list(self.sample_columns),
            "interactive": params['interactive_plots'],
            "save_figs": params['save_plots'],
            "text": texts,
            "markers": markers,
            "colors": colors,
            "color_legend": color_legend,
            "marker_legend": marker_legend
        }

    def QAQC(self, params):
        """
        This is the wrapper for all the qcqa functions. 

        Args:
            tag (str, optional): Sample type must match this field. Defaults to None.
            sort (bool, optional): if true, sort the sample names. Defaults to False.
            interactive (bool, optional): if True, interactive plots are generated. Defaults to False.

            These args control if the named step is performed or not in the curation:

            pca (bool, optional): Defaults to False.
            tsne (bool, optional): Defaults to False.
            pearson (bool, optional): Defaults to False.
            spearman (bool, optional): Defaults to False.
            kendall (bool, optional): Defaults to False.
            missing_feature_percentiles (bool, optional): Defaults to False.
            missing_feature_distribution (bool, optional): Defaults to False.
            median_correlation_outlier_detection (bool, optional): Defaults to False.
            missing_feature_outlier_detection (bool, optional): Defaults to False.
            intensity_analysis (bool, optional): Defaults to False.
            feature_distribution (bool, optional): Defaults to False.
            feature_outlier_detection (bool, optional): Defaults to False.

        Returns:
            list: with all qcqa results for the performed QCQA steps
        """
        self.generate_figure_params(params)
        qaqc_result = []
        for name, method in self.method_map.items():
            if (name in params and params[name]) or ('all' in params and params['all']):
                try:
                    result = method()
                    if type(result) is list:
                        qaqc_result.extend(result)
                    else:
                        qaqc_result.append(result)
                except RecursionError:
                    import sys
                    sys.setrecursionlimit(100000)
                    try:
                        result = method()
                        if type(result) is list:
                            qaqc_result.extend(result)
                        else:
                            qaqc_result.append(result)
                    except Exception as e:
                        print("Failure Executing Method: " + name)
                        print(e)
                except Exception as e:
                    print("Failure Executing Method: " + name)
                    print(e)
        return qaqc_result
