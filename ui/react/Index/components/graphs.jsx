import React from 'react';
import PropTypes from 'prop-types';

import { DateTime } from 'luxon';

import Grid from '@mui/material/Grid';
import Typography from '@mui/material/Typography';
import { makeStyles } from '@mui/styles';

import {
  XYPlot,
  MarkSeries,
  XAxis,
  YAxis,
  Hint,
  HorizontalGridLines,
  makeWidthFlexible,
  Highlight,
} from 'react-vis';

import { GraphControls } from './controls';
import { MacInteraction } from './card';

import '../../../../node_modules/react-vis/dist/style.css';

const styles = makeStyles({
  plot: {
    '&:hover': {
      cursor: 'pointer'
    }
  }
});

const FlexibleXYPlot = makeWidthFlexible(XYPlot);

export const Graph = ({
  data,
  MacTimestampData,
  numMacs,
  count,
  setCount,
  timeframe,
  setTimeframe,
  selectMac,
  selectedMac,
  setFilter,
  graphHeight,
  setGraphHeight,
  plotAll,
  setPlotAll,
  loading,
}) => {
  const classes = styles();
  const [highlight, setHighlight] = React.useState(false);
  const [showTag, setShowTag] = React.useState(false);
  const [showName, setShowName] = React.useState(false);
  const [showVendor, setShowVendor] = React.useState(false);
  const [hintText, setHintText] = React.useState(false);

  const formatHint = (value) => {
    setHintText({
      ...value,
      time: value.x.toLocaleString(),
      address: value.y,
    });
  };

  const formatMacTick = (value) => {
    const dataLookup = data.find((d) => d.addr === value);
    const mockObject = { y: value };

    const fillColor = dataLookup.ouid.category?.color;

    const extraProps = fillColor ? { fill: fillColor, fontWeight: 'bold' } : { fontWeight: 'bold' };
    
    return (
      <tspan className={classes.plot}>
        <a onClick={() => selectMac(mockObject)}>
          {showTag && (
            dataLookup.tag !== 'None' && <tspan x='0' style={{fontWeight: 'bold'}}>({dataLookup.tag})</tspan>
          )}
          {showName && (
            dataLookup.name && <tspan x='0' dy='1em'>{dataLookup.name}</tspan>
          )}
          <tspan x='0' dy='1em' {...extraProps}>
            {dataLookup.ouid.category?.icon && (
              <tspan>{dataLookup.ouid.category.icon}</tspan>
            )}
            {showVendor ? (
              dataLookup.ouid?.vendor || value
            ) : value }
          </tspan>
          <tspan x='0' dy='1em' >
            ({dataLookup.seen})
          </tspan>
        </a>
      </tspan>
    );
  };

  const handleCountChange = (e) => setCount(e.target.value);
  const handleTimeframeChange = (e) => setTimeframe(e.target.value);
  const domainOverride = (!plotAll && timeframe !== 'all')
    ? [DateTime.now().minus({ minutes: timeframe }), DateTime.now()]
    : false

  const hintsEnabled = data.flat().length < 3000;

  return (
    <Grid item xs={12}>
      <Grid container spacing={2}>
        {selectedMac && (
          <Grid item xs={12} sm={3}>
            <MacInteraction
              close={() => selectMac(false)}
              selectMac={selectMac}
              selectedMac={selectedMac}
            />
          </Grid>
        )}
        <Grid item xs={12} sm={selectedMac ? 9 : 12}>
          <GraphControls
            count={count}
            timeframe={timeframe}
            showTag={showTag}
            setShowTag={setShowTag}
            showName={showName}
            setShowName={setShowName}
            handleCountChange={handleCountChange}
            handleTimeframeChange={handleTimeframeChange}
            setFilter={setFilter}
            graphHeight={graphHeight}
            setGraphHeight={setGraphHeight}
            plotAll={plotAll}
            setPlotAll={setPlotAll}
            loading={loading}
            dataCount={numMacs}
            showVendor={showVendor}
            setShowVendor={setShowVendor}
          />
          {MacTimestampData.length ? (
            <FlexibleXYPlot
              margin={{ left: 200 }}
              height={graphHeight}
              xType='time' 
              yType='ordinal'
              xDomain={
                highlight && [
                  highlight.left,
                  highlight.right
                ]
                ||
                domainOverride
              }
            >
              <XAxis title='Time'/>
              <HorizontalGridLines />
              <Highlight
                enableY={false}
                onBrushEnd={area => setHighlight(area)}
                onDrag={area => {
                  setHighlight({
                    left: highlight.left - (area.right - area.left),
                    right: highlight.right - (area.right - area.left),
                  })
                }}
              />
              <YAxis
                title='Mac Addr'
                tickFormat={formatMacTick}
              />
              <MarkSeries
                colorType='literal'
                onValueClick={(v) => selectMac(v)}
                onValueMouseOver={(v) => hintsEnabled && formatHint(v)}
                onValueMouseOut={() => setHintText(false)}
                data={MacTimestampData.flat()}
              />
              {hintText && <Hint value={hintText} />}
            </FlexibleXYPlot>
          ) : (
            <Typography variant='h5'>No Results</Typography>
          )}
        </Grid>
      </Grid>

    </Grid>
  );
};

Graph.propTypes = {
  count: PropTypes.oneOfType([
    PropTypes.bool,
    PropTypes.number,
    PropTypes.string
  ]),
  setCount: PropTypes.func,
  data: PropTypes.array,
  MacTimestampData: PropTypes.array,
  numMacs: PropTypes.number,
  setTime: PropTypes.func,
  timeframe: PropTypes.oneOfType([
    PropTypes.bool,
    PropTypes.number,
    PropTypes.string
  ]),
  setTimeframe: PropTypes.func,
  selectMac: PropTypes.func,
  selectedMac: PropTypes.oneOfType([
    PropTypes.bool,
    PropTypes.object,
  ]),
  setFilter: PropTypes.func,
  graphHeight: PropTypes.number,
  setGraphHeight: PropTypes.func,
  plotAll: PropTypes.bool,
  setPlotAll: PropTypes.func,
  loading: PropTypes.bool,
};

export default Graph;
